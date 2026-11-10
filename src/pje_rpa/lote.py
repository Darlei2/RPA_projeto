"""Execucao geral: escolhe o tribunal, le a planilha e faz o fluxo inteiro, um advogado por vez.

Para cada linha da planilha:
  1. cadastro com o A1 (login, formulario, termo de compromisso assinado);
  2. sai do PJe e pede "Solicitar nova senha" (CPF + e-mail);
  3. le o link na caixa de e-mail (IMAP) ou recebe o link colado;
  4. cadastra uma senha forte gerada pelo robo;
  5. testa o login com CPF + senha + 2FA.
No fim, grava entregas\\credenciais-<data>.xlsx protegida por senha e mostra um resumo.

Comando: scripts\\executar.ps1 (ou `python -m pje_rpa executar`).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .entrega import LinhaCredencial
from .models import AppConfig

PASTA_TRIBUNAIS = Path("tribunais")
PLANILHA_PADRAO = Path("planilhas") / "advogados.xlsx"


# --------------------------------------------------------------- escolha do tribunal
@dataclass
class OpcaoTribunal:
    chave: str          # nome do arquivo (ex.: tjap)
    caminho: Path
    nome: str           # ex.: TJAP - 1o grau
    liberado: bool      # envio real liberado no .toml


def listar_tribunais(pasta: Path = PASTA_TRIBUNAIS) -> list[OpcaoTribunal]:
    from .config import load_config

    opcoes = []
    for path in sorted(pasta.glob("*.toml")):
        if path.name.startswith("_"):
            continue
        try:
            cfg = load_config(path)
        except Exception:  # noqa: BLE001 - arquivo quebrado nao derruba a lista
            continue
        opcoes.append(OpcaoTribunal(path.stem, path, cfg.tribunal,
                                    cfg.selectors_approved and cfg.allow_live_submission))
    return opcoes


def escolher_tribunal(opcoes: list[OpcaoTribunal], resposta: str) -> OpcaoTribunal | None:
    """Aceita o numero da lista, o nome do arquivo (tjap) ou o nome (TJAP - 1o grau)."""
    resposta = (resposta or "").strip()
    if resposta.isdigit() and 1 <= int(resposta) <= len(opcoes):
        return opcoes[int(resposta) - 1]
    alvo = re.sub(r"[^a-z0-9]", "", resposta.casefold())
    for opcao in opcoes:
        if alvo in (opcao.chave.casefold(), re.sub(r"[^a-z0-9]", "", opcao.nome.casefold())):
            return opcao
    candidatos = [o for o in opcoes if alvo and alvo in re.sub(r"[^a-z0-9]", "", o.nome.casefold())]
    return candidatos[0] if len(candidatos) == 1 else None


# ------------------------------------------------------------------- execucao
@dataclass
class ResultadoAdvogado:
    linha: int
    nome: str
    cadastro: str = "-"
    motivo: str = ""
    credencial: LinhaCredencial | None = None

    @property
    def login_ok(self) -> bool:
        return bool(self.credencial and self.credencial.login_testado)


@dataclass
class Dependencias:
    """Pontos de troca para os testes (no uso real ficam os padroes)."""
    ask: Callable[[str], str]
    secret: Callable[[str], str]
    yes: Callable[[str, bool], bool]
    cadastrar: Callable[..., object]
    credencial: Callable[..., LinhaCredencial]
    abrir_caixa: Callable[..., object]
    entregar: Callable[[list[LinhaCredencial], Path], None]
    store_factory: Callable[[], object] | None = None
    log: Callable[[str], None] = print
    extras: dict = field(default_factory=dict)


def _dependencias_padrao() -> Dependencias:
    from . import senha_cli
    from .execucao import cadastrar

    return Dependencias(
        ask=senha_cli._ask,
        secret=lambda prompt: senha_cli._secret(prompt, allow_empty=True),
        yes=lambda prompt, default=False: senha_cli._yes(prompt, default),
        cadastrar=cadastrar,
        credencial=lambda config, audit_root, lawyer, inbox, secret_2fa: senha_cli._one_tribunal(
            config, audit_root, lawyer, inbox, secret_2fa, pause=False),
        abrir_caixa=_abrir_caixa,
        entregar=senha_cli._deliver,
    )


def _abrir_caixa(email: str, host: str, user: str, password: str, deps: Dependencias):
    """Caixa de e-mail do advogado: dados da planilha, senao pergunta (como no criar-senha)."""
    from . import senha_cli
    from .email_inbox import ImapInbox, guess_imap_host

    if not password:
        return senha_cli._setup_inbox(email)
    inbox = ImapInbox(host=host or guess_imap_host(email) or "", user=user or email,
                      password=re.sub(r"\s", "", password))
    try:
        inbox.check_login()
        deps.log("OK: caixa de e-mail acessada")
        return inbox
    except Exception as exc:  # noqa: BLE001
        deps.log(f"FALHA na caixa de e-mail da planilha: {senha_cli._imap_reason(exc, len(password))}")
        return senha_cli._setup_inbox(email)


def _code_provider(secret_2fa: str, deps: Dependencias):
    from .senha_cli import _code_provider as provider

    return provider(secret_2fa) if secret_2fa else (lambda msg: deps.ask(f"{msg}: ") or None)


def _chave_2fa(linha, deps: Dependencias) -> str:
    from .senha_cli import _ask_2fa_key, _clean_2fa_key
    from .totp import normalizar_chave

    raw = linha.segredo("chave_2fa")
    if raw:
        try:
            return normalizar_chave(_clean_2fa_key(raw))
        except ValueError:
            deps.log("  chave_2fa da planilha invalida; sera perguntada")
    return _ask_2fa_key() if deps.extras.get("perguntar_2fa", True) else ""


def processar_advogado(config: AppConfig, linha, audit_root: Path, deps: Dependencias, *,
                       rehearsal: bool, nova_senha_se_ja_cadastrado: bool) -> ResultadoAdvogado:
    res = ResultadoAdvogado(linha.numero_linha, linha.nome)
    record = linha.record
    deps.log(f"\n========== Linha {linha.numero_linha}: {linha.nome} ({config.tribunal}) ==========")

    pfx_password = linha.segredo("senha_certificado") or deps.secret(
        f"Senha do certificado A1 de {linha.nome}")
    if not pfx_password:
        res.cadastro, res.motivo = "PULADO", "senha do certificado nao informada"
        return res
    secret_2fa = _chave_2fa(linha, deps)

    # 1) Cadastro com o certificado
    deps.log("1/2 Cadastro com o certificado A1")
    kwargs = {"rehearsal": rehearsal, "notify": deps.log}
    if deps.store_factory:
        kwargs["store"] = deps.store_factory()
    resultado = deps.cadastrar(config, record, linha.certificado.read_bytes(), pfx_password,
                               _code_provider(secret_2fa, deps), audit_root, **kwargs)
    pfx_password = ""
    res.cadastro, res.motivo = resultado.status, resultado.reason
    deps.log(f"Cadastro: {resultado.status} - {resultado.reason}")

    pode_senha = resultado.status == "COMPLETED" or (
        resultado.status == "ALREADY_REGISTERED" and nova_senha_se_ja_cadastrado)
    if rehearsal or not pode_senha:
        if resultado.status == "ALREADY_REGISTERED" and not rehearsal:
            res.motivo += " (senha nao alterada: opcao desligada)"
        return res

    # 2) Senha: pedido do link, e-mail, nova senha e teste do login
    deps.log("2/2 Credencial: nova senha pelo e-mail e teste de CPF + senha + 2FA")
    inbox = deps.abrir_caixa(record.email, linha.segredo("imap_servidor"),
                             linha.segredo("imap_usuario"), linha.segredo("imap_senha"), deps)
    lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
              "chave_2fa_exibir": secret_2fa if deps.extras.get("chave_na_planilha", True) else ""}
    res.credencial = deps.credencial(config, audit_root, lawyer, inbox, secret_2fa)
    secret_2fa = ""
    return res


def executar_lote(config: AppConfig, linhas: list, audit_root: Path, saida: Path, deps: Dependencias, *,
                  rehearsal: bool, nova_senha_se_ja_cadastrado: bool) -> list[ResultadoAdvogado]:
    from .execucao import limpar_pendentes

    try:  # certificados que uma execucao anterior deixou no repositorio do Windows
        if deps.store_factory:
            store = deps.store_factory()
        else:
            from .certificate import WindowsCertificateStore
            store = WindowsCertificateStore()
        limpar_pendentes(audit_root, store, deps.log)
    except Exception:  # noqa: BLE001 - fora do Windows nao ha repositorio
        pass

    resultados: list[ResultadoAdvogado] = []
    try:
        for i, linha in enumerate(linhas, start=1):
            deps.log(f"\n[{i}/{len(linhas)}]")
            try:
                resultados.append(processar_advogado(
                    config, linha, audit_root, deps, rehearsal=rehearsal,
                    nova_senha_se_ja_cadastrado=nova_senha_se_ja_cadastrado))
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - um advogado com problema nao para o lote
                resultados.append(ResultadoAdvogado(linha.numero_linha, linha.nome, "ERROR",
                                                    f"{type(exc).__name__}: {exc}"))
    except KeyboardInterrupt:
        deps.log("\nInterrompido pelo operador: os advogados ja processados vao para a planilha.")
    finally:
        credenciais = [r.credencial for r in resultados if r.credencial]
        if credenciais:
            deps.entregar(credenciais, saida)
    _resumo(resultados, config, rehearsal, deps.log)
    return resultados


def _resumo(resultados: list[ResultadoAdvogado], config: AppConfig, rehearsal: bool, log) -> None:
    log(f"\n================ RESUMO - {config.tribunal}{' (ENSAIO)' if rehearsal else ''} ================")
    for r in resultados:
        cred = "-"
        if r.credencial:
            c = r.credencial
            cred = ("senha OK" if c.senha_cadastrada else "senha NAO cadastrada") + \
                   (", login CPF+senha+2FA OK" if c.login_testado else ", login nao testado/falhou")
            if c.observacao:
                cred += f" ({c.observacao})"
        log(f"  linha {r.linha} | {r.nome} | cadastro: {r.cadastro} | credencial: {cred}")
        if r.motivo and r.cadastro not in ("COMPLETED",):
            log(f"      motivo: {r.motivo}")
    prontos = sum(1 for r in resultados if r.login_ok)
    log(f"\nCredenciais prontas (cadastro + senha + login testado): {prontos} de {len(resultados)}")


# ------------------------------------------------------------- assistente
def run_executar(audit_root: Path = Path("runtime/audit"), saida: Path = Path("entregas"),
                 tribunal: str = "", planilha: str = "", ensaio: bool | None = None,
                 deps: Dependencias | None = None) -> int:
    from .config import load_config
    from .planilha import gravar_modelo, ler_planilha

    deps = deps or _dependencias_padrao()
    log = deps.log

    # 1) Tribunal
    opcoes = listar_tribunais()
    if not opcoes:
        log("Nenhum tribunal configurado em tribunais\\")
        return 2
    escolhido = escolher_tribunal(opcoes, tribunal) if tribunal else None
    while escolhido is None:
        log("\nTribunais disponiveis:")
        for i, o in enumerate(opcoes, start=1):
            log(f"  {i:>2}. {o.chave:<8} {o.nome:<28} {'[envio real liberado]' if o.liberado else '[so ensaio]'}")
        escolhido = escolher_tribunal(opcoes, deps.ask("\nQual tribunal? (numero ou nome, ex.: tjap): "))
        if escolhido is None:
            log("Tribunal nao reconhecido, tente de novo.")
    config = load_config(escolhido.caminho)
    log(f"\nTribunal: {config.tribunal}  ({config.base_url})")

    # 2) Modo
    if ensaio is None:
        if not escolhido.liberado:
            log("Envio real NAO liberado neste tribunal: sera ENSAIO (entra, confere e preenche, nao envia).")
            log(f"Para liberar: selectors_approved = true e allow_live_submission = true em {escolhido.caminho}")
            ensaio = True
        else:
            ensaio = not deps.yes("Executar em modo REAL (cadastra, assina o termo e cria a senha)?", True)
    elif not ensaio and not escolhido.liberado:
        log(f"Envio real nao liberado em {escolhido.caminho}. Rode em ensaio ou libere o tribunal.")
        return 2
    log("Modo: " + ("ENSAIO (nada e enviado nem assinado)" if ensaio else "REAL"))

    # 3) Planilha
    caminho = Path(planilha) if planilha else None
    while caminho is None or not caminho.exists():
        if caminho is not None:
            log(f"Planilha nao encontrada: {caminho}")
            if not caminho.exists() and caminho == PLANILHA_PADRAO and deps.yes(
                    f"Criar a planilha modelo em {PLANILHA_PADRAO} para voce preencher?", True):
                gravar_modelo(PLANILHA_PADRAO)
                log(f"Modelo criado: {PLANILHA_PADRAO}. Preencha (uma linha por advogado) e rode de novo.")
                return 0
        resposta = deps.ask(f"Planilha dos advogados [{PLANILHA_PADRAO}]: ").strip().strip('"')
        caminho = Path(resposta) if resposta else PLANILHA_PADRAO
    linhas = ler_planilha(caminho, password_provider=lambda: deps.secret("Senha para abrir a planilha de advogados"))
    validas = [l for l in linhas if l.ok]
    invalidas = [l for l in linhas if not l.ok]
    log(f"\nPlanilha: {caminho}  -> {len(validas)} advogado(s) pronto(s), {len(invalidas)} com erro")
    for l in invalidas:
        log(f"  linha {l.numero_linha} ({l.nome or 'sem nome'}): " + "; ".join(l.erros))
    if not validas:
        log("Nada para executar.")
        return 2
    for l in validas:
        log(f"  linha {l.numero_linha}: {l.nome}  (OAB {l.record.oab_numero}/{l.record.oab_uf})")

    # 4) Opcoes e confirmacao
    nova_senha = False
    if not ensaio:
        nova_senha = deps.yes("Se o advogado JA tiver cadastro neste tribunal, criar uma NOVA senha mesmo assim?", False)
        log("Abra o PJeOffice PRO antes de continuar (ele assina o login e o termo).")
    if not deps.yes(f"Iniciar {'ENSAIO' if ensaio else 'CADASTRO REAL'} de {len(validas)} advogado(s) "
                    f"no {config.tribunal}?", True):
        log("Cancelado.")
        return 1

    audit_root.mkdir(parents=True, exist_ok=True)
    resultados = executar_lote(config, validas, audit_root, saida, deps, rehearsal=ensaio,
                               nova_senha_se_ja_cadastrado=nova_senha)
    if ensaio:
        return 0 if resultados and all(r.cadastro == "REHEARSAL_OK" for r in resultados) else 3
    return 0 if resultados and all(r.login_ok for r in resultados) else 3
