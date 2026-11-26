"""Validacao dos enderecos de acesso: responsabilidade SEPARADA da identidade do tribunal.

A identidade (registro.py) diz QUAL tribunal e; este modulo diz se o robo pode COMECAR a navegar
no endereco configurado (base_url do .toml). Nenhuma regra de negocio depende do endereco.

Regras, para ensaio e envio real:
  1. https, com host, sem usuario/senha nem porta no endereco;
  2. host oficial (.jus.br) e listado em hosts_autorizados do tribunal (registro ou secao [tribunal]);
  3. nao pode ser o login unificado (sso.cloud.pje.jus.br) nem levar parametros de uma sessao ou
     fluxo de login (state, code, nonce, redirect_uri...). O endereco de partida e o do tribunal; o
     login unificado, o certificado, o CAPTCHA e o 2FA sao alcancados pela navegacao normal e
     nenhum deles e pulado;
  4. sem "exemplo"/"example".
Envio real, alem disso: o endereco tem de ser o endereco_padrao do registro OU o que o operador
aprovou explicitamente (`python -m pje_rpa enderecos --aprovar <id>`, que grava endereco_aprovado
no .toml). Mudar o endereco nunca libera o envio real sozinho.

As mensagens citam host e nomes de parametros, nunca os valores da consulta.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

HOSTS_DE_LOGIN_COMPARTILHADO = frozenset({"sso.cloud.pje.jus.br"})
# parametros de um pedido de login OpenID/Keycloak ou de uma sessao ja aberta
PARAMETROS_DE_SESSAO = frozenset({
    "state", "nonce", "code", "session_state", "session_code", "execution", "tab_id", "client_data",
    "kc_action", "redirect_uri", "response_type", "client_id", "auth_session_id", "jsessionid", "cid",
})
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def problemas_do_host(host: str) -> list[str]:
    host = (host or "").strip().lower()
    if not _HOST.match(host):
        return [f"host '{host}' invalido"]
    if not host.endswith(".jus.br"):
        return [f"host '{host}' nao e oficial (.jus.br)"]
    if host in HOSTS_DE_LOGIN_COMPARTILHADO:
        return [f"host '{host}' e o login unificado do PJe, nao o endereco de um tribunal"]
    return []


def problemas_do_endereco(url: str, hosts_autorizados) -> list[str]:
    """Problemas do endereco de partida (lista vazia = valido)."""
    url = (url or "").strip()
    if not url:
        return ["endereco vazio"]
    if any(c in url for c in '"\\ ') or any(ord(c) < 32 for c in url):
        return ["endereco com caracteres invalidos (aspas, barra invertida, espaco ou controle)"]
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError:
        return ["endereco mal formado"]
    host = (partes.hostname or "").lower()
    erros: list[str] = []
    if partes.scheme != "https" or not host:
        erros.append("o endereco tem de ser https e ter host")
    if partes.username is not None or partes.password is not None:
        erros.append("o endereco nao pode conter usuario ou senha")
    if porta not in (None, 443):
        erros.append(f"porta {porta} nao permitida")
    if "exemplo" in url.lower() or "example" in url.lower():
        erros.append("substitua o endereco de exemplo pelo oficial do tribunal")
    if host:
        erros += problemas_do_host(host)
        if host in HOSTS_DE_LOGIN_COMPARTILHADO:
            erros.append("comece pelo endereco do tribunal: o login unificado e alcancado pela navegacao, "
                         "com certificado, CAPTCHA e 2FA normais")
        hosts = [h.lower() for h in (hosts_autorizados or ())]
        if not hosts:
            erros.append("o tribunal nao tem hosts autorizados (registro ou secao [tribunal])")
        elif host not in hosts:
            erros.append(f"host '{host}' nao esta entre os autorizados para este tribunal ({', '.join(hosts)})")
    nomes = {nome.lower() for nome, _ in parse_qsl(partes.query, keep_blank_values=True)}
    nomes |= {p.split("=", 1)[0].lower() for p in partes.path.split(";")[1:]}
    sessao = sorted(nomes & PARAMETROS_DE_SESSAO)
    if sessao:
        erros.append("o endereco leva parametros de uma sessao ou de um pedido de login ("
                     + ", ".join(sessao) + "): use o endereco de entrada do tribunal")
    return list(dict.fromkeys(erros))


def problemas(config) -> list[str]:
    """Problemas do base_url do config (AppConfig ou qualquer objeto com base_url/hosts_autorizados)."""
    return problemas_do_endereco(getattr(config, "base_url", ""), getattr(config, "hosts_autorizados", ()))


def aprovado_para_envio_real(config) -> bool:
    """O endereco e o de referencia do registro ou o aprovado explicitamente pelo operador."""
    from .registro import normalizar_endereco

    atual = normalizar_endereco(getattr(config, "base_url", ""))
    referencias = {normalizar_endereco(getattr(config, "endereco_padrao", "")),
                   normalizar_endereco(getattr(config, "endereco_aprovado", ""))} - {""}
    return bool(atual) and atual in referencias


def pendencia_do_envio_real(config) -> str:
    """'' quando o endereco pode ser usado no envio real; senao, o que falta (para o operador)."""
    erros = problemas(config)
    if erros:
        return "endereco invalido: " + "; ".join(erros)
    if not aprovado_para_envio_real(config):
        tid = getattr(config, "tribunal_id", "") or "<id>"
        return (f"o endereco de login de {tid} e diferente do endereco de referencia e ainda nao foi aprovado "
                f"para o envio real. Confira e aprove: python -m pje_rpa enderecos --aprovar {tid}")
    return ""


# ------------------------------------------------------------------ aprovacao (grava no .toml)
def gravar_aprovacao(caminho: Path, url: str) -> None:
    """Grava `endereco_aprovado = "<url>"` na secao [app] do proprio .toml (troca o que houver).
    Edicao de texto linha a linha: preserva comentarios e o resto do arquivo."""
    if any(c in url for c in '"\\') or any(ord(c) < 32 for c in url):
        raise ValueError("endereco com caracteres que nao podem ser gravados")
    with caminho.open(encoding="utf-8", newline="") as arquivo:  # preserva CRLF dos .toml da VM
        texto = arquivo.read()
    fim = "\r\n" if "\r\n" in texto else "\n"
    linhas = texto.splitlines()
    nova = f'endereco_aprovado = "{url}"'
    secao, inicio_app, fim_app = "", None, None
    for i, linha in enumerate(linhas):
        cab = re.match(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$", linha)
        if cab:
            if secao == "app" and fim_app is None:
                fim_app = i
            secao = cab.group(1).strip()
            if secao == "app":
                inicio_app = i
    if inicio_app is not None and fim_app is None:
        fim_app = len(linhas)
    if inicio_app is None:
        linhas += ["", "[app]", nova]
    else:
        alvo = [i for i in range(inicio_app + 1, fim_app) if re.match(r"^\s*endereco_aprovado\s*=", linhas[i])]
        if alvo:
            linhas[alvo[0]] = nova
            for i in reversed(alvo[1:]):
                del linhas[i]
        else:
            depois = [i for i in range(inicio_app + 1, fim_app) if re.match(r"^\s*base_url\s*=", linhas[i])]
            linhas.insert((depois[0] if depois else inicio_app) + 1, nova)
    caminho.write_text(fim.join(linhas) + fim, encoding="utf-8", newline="")


# ------------------------------------------------------------------ comando `enderecos`
def conferir(pasta: Path, log=print) -> int:
    """Mostra, por tribunal, a identidade e a situacao do endereco. 0 = tudo valido; 6 = ha
    arquivo ignorado, registro com erro ou endereco recusado."""
    from . import registro
    from .lote import listar_tribunais

    codigo = 0
    try:
        reg = registro.carregar(pasta) or {}
    except registro.RegistroInvalido as exc:
        log(f"REGISTRO COM ERRO: {exc}")
        return 6
    erros: list[str] = []
    opcoes = listar_tribunais(pasta, erros)
    log(f"{'id':<14} {'grau':<4} {'grupo':<7} situacao do endereco")
    for o in opcoes:
        from .config import load_config

        cfg = load_config(o.caminho)
        if o.problemas:
            situacao, codigo = "RECUSADO: " + "; ".join(o.problemas), 6
        elif normalizar(cfg.base_url) == normalizar(cfg.endereco_padrao):
            situacao = "ok (endereco de referencia)"
        elif aprovado_para_envio_real(cfg):
            situacao = "alterado e APROVADO para o envio real"
        else:
            situacao = ("alterado: ensaio permitido; envio real so depois de aprovar "
                        f"(python -m pje_rpa enderecos --aprovar {o.chave})")
        log(f"{o.chave:<14} {o.grau or '-':<4} {o.grupo_credencial or '-':<7} {situacao}")
        for aviso in cfg.avisos:
            log(f"{'':<14} aviso: {aviso}")
    for erro in erros:
        log(f"IGNORADO: {erro}")
        codigo = 6
    sem_arquivo = sorted(set(reg) - {o.chave for o in opcoes} - {e.split('.toml')[0] for e in erros})
    if sem_arquivo:
        log("No registro, sem arquivo .toml (nao sao executados): " + ", ".join(sem_arquivo))
    return codigo


def normalizar(url: str) -> str:
    from .registro import normalizar_endereco

    return normalizar_endereco(url)


def aprovar(pasta: Path, tribunal_id: str, confirmar, log=print, trilha: Path | None = None) -> int:
    """Aprova o endereco ATUAL do tribunal para o envio real (grava endereco_aprovado no .toml).
    Recusa endereco invalido. 0 = aprovado ou nada a fazer; 1 = cancelado; 6 = recusado."""
    import getpass
    import json
    from datetime import datetime

    from .config import ConfigurationError, load_config

    caminho = pasta / f"{tribunal_id}.toml"
    if tribunal_id.startswith("_") or not caminho.exists():
        log(f"Nao ha tribunais\\{tribunal_id}.toml.")
        return 6
    try:
        cfg = load_config(caminho)
    except ConfigurationError as exc:
        log(f"{caminho.name} com erro: {exc}")
        return 6
    erros = problemas(cfg)
    if erros:
        log(f"Endereco de {tribunal_id} RECUSADO, nada foi aprovado: " + "; ".join(erros))
        return 6
    if aprovado_para_envio_real(cfg):
        log(f"{tribunal_id}: o endereco atual ja vale para o envio real. Nada a fazer.")
        return 0
    log(f"{cfg.tribunal} ({tribunal_id})\n  endereco de referencia: {cfg.endereco_padrao or '(nenhum)'}\n"
        f"  endereco atual:         {cfg.base_url}")
    if not confirmar("Confirmou no navegador que este e o endereco oficial do tribunal e quer liberar o "
                     "envio real nele?"):
        log("Cancelado. Nada foi alterado.")
        return 1
    gravar_aprovacao(caminho, cfg.base_url)
    trilha = trilha or Path("runtime") / "audit" / "enderecos-aprovados.jsonl"
    try:
        trilha.parent.mkdir(parents=True, exist_ok=True)
        with trilha.open("a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps({"quando": datetime.now().isoformat(timespec="seconds"),
                                      "tribunal_id": tribunal_id, "endereco": cfg.base_url,
                                      "operador": getpass.getuser()}, ensure_ascii=False) + "\n")
    except OSError as exc:
        log(f"ATENCAO: nao consegui registrar a aprovacao em {trilha} ({exc}).")
    log(f"Aprovado: {tribunal_id} pode usar este endereco no envio real (registro em {trilha}).")
    return 0
