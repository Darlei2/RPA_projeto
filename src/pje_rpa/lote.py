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
        # nome do arquivo tambem sem hifen: "trf5-jef" e "trf5jef" valem o mesmo
        if alvo in (re.sub(r"[^a-z0-9]", "", opcao.chave.casefold()),
                    re.sub(r"[^a-z0-9]", "", opcao.nome.casefold())):
            return opcao
    candidatos = [o for o in opcoes if alvo and alvo in re.sub(r"[^a-z0-9]", "", o.nome.casefold())]
    return candidatos[0] if len(candidatos) == 1 else None


def escolher_tribunais(opcoes: list[OpcaoTribunal], resposta: str) -> tuple[list[OpcaoTribunal], list[str]]:
    """Varios tribunais separados por virgula, ponto e virgula ou espaco: "8, 3, 31" ou
    "tremg, tjpa, trt5". Mantem a ordem digitada e ignora repetidos.
    Devolve (escolhidos, itens nao reconhecidos)."""
    escolhidos: list[OpcaoTribunal] = []
    invalidos: list[str] = []
    partes = [p for p in re.split(r"[,;]+", resposta or "") if p.strip()]
    if len(partes) == 1 and escolher_tribunal(opcoes, partes[0]) is None:
        partes = partes[0].split()  # "tremg tjpa trt5"
    for parte in partes:
        opcao = escolher_tribunal(opcoes, parte)
        if opcao is None:
            invalidos.append(parte.strip())
        elif opcao not in escolhidos:
            escolhidos.append(opcao)
    return escolhidos, invalidos


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


def _abrir_caixa(email: str, host: str, user: str, password: str, deps: Dependencias,
                 email_senha: str = "", email_2fa: str = ""):
    """Caixa de e-mail do advogado: IMAP sempre que houver senha IMAP; senao, Gmail pelo navegador.

    Senha da caixa, nesta ordem: coluna imap_senha; senha de app ja gerada (cofre local); senha
    de app gerada agora pelo robo no Gmail com email_senha + email_2fa da planilha. Sem nenhuma
    delas (ou se a caixa recusar a senha IMAP), a rota reserva le o Gmail pelo navegador com
    email_senha + email_2fa (gmail_web: sessao salva por conta); por ultimo, pergunta.
    """
    from . import cofre_local, senha_cli
    from .email_inbox import ImapInbox, guess_imap_host

    gerada = False
    web_senha, web_2fa = email_senha, email_2fa  # para a rota reserva (Gmail pelo navegador)
    if deps.extras.get("senha_via_email"):
        # Rota de e-mail com IMAP cadastrado a mao: usa a imap_senha da planilha ou o cofre e
        # nao gera senha de app. Sem IMAP, cai direto na rota reserva pelo navegador.
        email_senha = email_2fa = ""
    if not password:
        password = cofre_local.ler(email)
    if not password and email_senha and email_2fa:
        password, gerada = _senha_de_app_gmail(email, email_senha, email_2fa, deps), True
    if not password:
        return _caixa_gmail_web(email, web_senha, web_2fa, deps,
                                "sem senha IMAP para esta conta") or senha_cli._setup_inbox(email)
    inbox = ImapInbox(host=host or guess_imap_host(email) or "", user=user or email,
                      password=re.sub(r"\s", "", password))
    try:
        inbox.check_login()
        deps.log("OK: caixa de e-mail acessada")
        if gerada:
            if cofre_local.guardar(email, password):
                deps.log("  senha de app guardada no cofre do Windows: proximas execucoes nao entram no Gmail")
        return inbox
    except Exception as exc:  # noqa: BLE001
        deps.log(f"FALHA na caixa de e-mail: {senha_cli._imap_reason(exc, len(password))}")
        if not gerada and email_senha and email_2fa and cofre_local.ler(email):
            cofre_local.apagar(email)  # senha de app revogada: gera outra
            deps.log("  a senha de app guardada nao vale mais; gerando outra")
            return _abrir_caixa(email, host, user, "", deps, email_senha, email_2fa)
        return _caixa_gmail_web(email, web_senha, web_2fa, deps,
                                "a caixa recusou a senha IMAP") or senha_cli._setup_inbox(email)


@dataclass
class CaixaPendente:
    """Conta sem IMAP em que o Google exigiu verificacao no login pelo navegador: fica pendente."""
    motivo: str


def _caixa_gmail_web(email: str, senha: str, chave: str, deps: Dependencias, por_que: str):
    """Rota reserva: Gmail pelo navegador. None = nao se aplica (nao e Gmail ou falta senha/chave 2FA
    do e-mail). Se o Google barrar o login, registra a pendencia (na rota pelo e-mail) em vez de insistir."""
    from .gmail_senha_app import GmailError, e_gmail
    from .gmail_web import GmailWebInbox

    if not (e_gmail(email) and senha and chave):
        return None

    def assist(motivo: str) -> str:
        deps.log(f"\n  >>> Gmail: {motivo}.")
        deps.log("      O robo NAO contorna isso. Abri um Edge normal com o perfil desta conta: faca o login nele")
        deps.log("      (e-mail, senha e codigo do autenticador), espere a caixa abrir e FECHE essa janela.")
        return deps.ask("  [Enter] fiz o login e fechei a janela | [P] desistir: ")

    deps.log(f"Rota reserva ({por_que}): lendo o Gmail pelo navegador...")
    caixa = GmailWebInbox(email, senha, chave, assist=assist if deps.extras.get("assistido") else None)
    try:
        caixa.check_login()
    except GmailError as exc:
        deps.log(f"FALHA no Gmail pelo navegador: {exc}")
        return CaixaPendente(str(exc)) if deps.extras.get("senha_via_email") else None
    como = ("login feito a mao" if caixa.login_manual else
            "login automatico" if caixa.digitado else "sessao salva, sem novo login")
    deps.log(f"OK: caixa de e-mail acessada pelo navegador ({como})")
    return caixa


def _senha_de_app_gmail(email: str, senha: str, chave: str, deps: Dependencias) -> str:
    from .gmail_senha_app import GmailError, e_gmail, gerar_senha_de_app

    if not e_gmail(email):
        deps.log("  email_senha/email_2fa so sao usados em contas Gmail; para outros provedores use imap_senha")
        return ""

    def assist(motivo: str) -> str:
        deps.log(f"\n  >>> Gmail: {motivo}.")
        deps.log("      Termine no navegador aberto e tecle Enter, ou cole a senha de app de 16 letras se ja gerou.")
        return deps.secret("  Senha de app (Enter = terminei no navegador)")

    deps.log("Gerando a senha de app do Gmail (so na primeira vez desta conta)...")
    try:
        return gerar_senha_de_app(email, senha, chave, assist=assist if deps.extras.get("assistido") else None)
    except GmailError as exc:
        deps.log(f"FALHA ao gerar a senha de app do Gmail: {exc}")
        return ""


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

    # Segredos pedidos uma vez por advogado e reaproveitados nos outros tribunais da execucao
    # (so em memoria; o cache e apagado no fim de run_executar).
    cache = deps.extras.setdefault("cache", {}).setdefault(linha.numero_linha, {})
    if linha.usa_windows or deps.extras.get("so_senha"):
        pfx, pfx_password = None, ""  # certificado ja instalado (ou nao usado): sem arquivo e sem senha
    else:
        pfx = linha.certificado.read_bytes()
        if "pfx_password" not in cache:
            cache["pfx_password"] = linha.segredo("senha_certificado") or deps.secret(
                f"Senha do certificado A1 de {linha.nome}")
        pfx_password = cache["pfx_password"]
        if not pfx_password:
            res.cadastro, res.motivo = "PULADO", "senha do certificado nao informada"
            return res
    if "secret_2fa" not in cache:
        cache["secret_2fa"] = _chave_2fa(linha, deps)
    secret_2fa = cache["secret_2fa"]

    if deps.extras.get("so_senha"):
        res.cadastro, res.motivo = "NAO_TENTADO", "opcao so senha: cadastro nao conferido"
        deps.log("1/2 Cadastro: pulado (opcao so senha, sem login com certificado)")
        return _etapa_senha(config, linha, audit_root, deps, res, cache, secret_2fa)

    # 1) Cadastro com o certificado
    deps.log("1/2 Cadastro com o certificado A1" + (" (instalado no Windows)" if linha.usa_windows else " (.pfx)"))
    kwargs = {"rehearsal": rehearsal, "notify": deps.log, "thumbprint": linha.thumbprint}
    if not rehearsal and not deps.extras.get("senha_via_email"):
        # Senha pelo login unico (PDPJ), sem e-mail, na mesma sessao do certificado; se o tribunal
        # nao oferecer, o robo cai no caminho do e-mail ("Esqueci minha senha").
        # Com extras["senha_via_email"], pula o login unico e vai direto pelo e-mail (IMAP da planilha).
        kwargs["senha_sso"] = True
        kwargs["senha_sso_ja_cadastrado"] = nova_senha_se_ja_cadastrado
    if deps.extras.get("assistido"):
        kwargs["assist"] = lambda report: _assistente(report, deps, rehearsal)
    if deps.store_factory:
        kwargs["store"] = deps.store_factory()
    resultado = deps.cadastrar(config, record, pfx, pfx_password,
                               _code_provider(secret_2fa, deps), audit_root, **kwargs)
    pfx_password = ""
    res.cadastro, res.motivo = resultado.status, resultado.reason
    deps.log(f"Cadastro: {resultado.status} - {resultado.reason}")
    _mostrar_diagnostico(audit_root, resultado, deps.log)

    pode_senha = resultado.status == "COMPLETED" or (
        resultado.status == "ALREADY_REGISTERED" and nova_senha_se_ja_cadastrado)
    if rehearsal or not pode_senha:
        if resultado.status == "ALREADY_REGISTERED" and not rehearsal:
            res.motivo += " (senha nao alterada: opcao desligada)"
        return res

    senha_sso = getattr(resultado, "senha", "")
    if senha_sso:
        deps.log("2/2 Credencial: senha criada pelo login unico (sem e-mail); teste de CPF + senha + 2FA")
        testar = deps.extras.get("testar_senha")
        if testar is None:
            from .senha_cli import testar_credencial as testar
        lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
                  "chave_2fa_exibir": secret_2fa if deps.extras.get("chave_na_planilha", True) else ""}
        res.credencial = testar(config, audit_root, lawyer, secret_2fa, senha_sso)
        senha_sso = ""
        if res.credencial.login_testado:
            return res
        deps.log("  a senha do login unico nao passou no teste de login")
    if deps.extras.get("sem_email"):
        # Execucao sem intervencao: nao cai no "Esqueci minha senha" (depende da caixa de e-mail).
        detalhe = getattr(resultado, "senha_detalhe", "") or "login unico indisponivel"
        if res.credencial is not None and not res.credencial.login_testado:
            # a senha foi enviada ao login unico mas o teste falhou: vai para a planilha protegida
            # como "nao testada" (nao se perde) e nao sobe como credencial pronta
            detalhe = "senha criada, mas o login de teste falhou: " + (res.credencial.observacao or "")
        res.motivo += f" | SENHA PENDENTE: {detalhe}"
        deps.log(f"2/2 Credencial: senha pendente ({detalhe}). E-mail desligado nesta execucao.")
        return res
    deps.log("  tentando pelo e-mail")
    return _etapa_senha(config, linha, audit_root, deps, res, cache, secret_2fa)


def _etapa_senha(config, linha, audit_root: Path, deps: Dependencias, res, cache: dict, secret_2fa: str):
    """2) Senha: pedido do link, e-mail, nova senha e teste do login."""
    record = linha.record
    deps.log("2/2 Credencial: nova senha pelo e-mail e teste de CPF + senha + 2FA")
    if "inbox" not in cache:
        cache["inbox"] = deps.abrir_caixa(record.email, linha.segredo("imap_servidor"),
                                          linha.segredo("imap_usuario"), linha.segredo("imap_senha"), deps,
                                          linha.segredo("email_senha"), linha.segredo("email_2fa"))
    inbox = cache["inbox"]
    if isinstance(inbox, CaixaPendente):
        # Sem como ler o e-mail desta conta: nao pede senha nova ao PJe (o link chegaria e se perderia).
        deps.log("PENDENTE: Gmail sem acesso (" + inbox.motivo + "). Pedido de senha NAO enviado ao PJe.")
        res.credencial = LinhaCredencial(config.tribunal, config.base_url, record.nome_esperado,
                                         record.cpf_esperado, record.email,
                                         observacao="PENDENTE - Gmail: " + inbox.motivo)
        return res
    lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
              "chave_2fa_exibir": secret_2fa if deps.extras.get("chave_na_planilha", True) else ""}
    res.credencial = deps.credencial(config, audit_root, lawyer, inbox, secret_2fa)
    secret_2fa = ""
    return res


ROTULOS_PENDENCIA = [
    ("ambiguous", "Campo com mais de um candidato"),
    ("non_editable", "Campo bloqueado para edicao"),
    ("unknown_required", "Campo obrigatorio sem dado na planilha"),
    ("other_subject", "Campo de outro assunto (ignorado pelo robo)"),
]


def _assistente(report: dict, deps: Dependencias, rehearsal: bool) -> str:
    """Modo assistido: mostra a duvida do robo e deixa o operador resolver no Edge aberto."""
    deps.log("\n  >>> O robo tem duvida neste formulario: " + ", ".join(report.get("pending", [])))
    for key, titulo in ROTULOS_PENDENCIA:
        for item in report.get(key) or []:
            deps.log(f"      {titulo}: {item}")
    sem_dado = [p.split(":", 1)[1] for p in report.get("pending", []) if p.startswith("campo_obrigatorio_sem_dado:")]
    if sem_dado:
        deps.log("      O PJe exige campo(s) que a planilha nao tem: " + ", ".join(sem_dado) + ". Preencha no Edge"
                 " agora e, para as proximas vezes, use as colunas rg_numero / rg_orgao / rg_data_expedicao.")
    deps.log("      O Edge esta aberto nessa tela. Voce pode preencher/corrigir o que faltar direto nele.")
    seguir = "continuar ate parar antes de enviar (ensaio)" if rehearsal else "continuar e ENVIAR o cadastro"
    while True:
        resposta = deps.ask(
            "  [Enter] ja corrigi, reavaliar | [C] o formulario esta certo, " + seguir + " | [P] parar: "
        ).strip().casefold()
        if resposta in ("", "r", "reavaliar"):
            return "reavaliar"
        if resposta in ("c", "continuar", "s"):
            return "seguir"
        if resposta in ("p", "parar", "n"):
            return "parar"


def _mostrar_diagnostico(audit_root: Path, resultado, log) -> None:
    """Quando o cadastro para, mostra onde esta o print e o que o formulario tinha de diferente."""
    import json

    if resultado.status not in ("NEEDS_HUMAN", "FAILED", "BLOCKED") or not getattr(resultado, "run_id", ""):
        return
    run_dir = audit_root / resultado.run_id
    for shot in sorted(run_dir.glob("tela-*.png")):
        log(f"  Print da tela: {shot}")
    try:
        for line in (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            details = event.get("details") or {}
            if event.get("event") == "adaptive_form_evaluated":
                for item in details.get("ambiguous") or []:
                    key, _, labels = str(item).partition(": ")
                    log(f"  Campo '{key}' ambiguo, candidatos: {labels}")
                for item in details.get("non_editable") or []:
                    key, _, state = str(item).partition(": ")
                    log(f"  Campo '{key}' bloqueado para edicao ({state})")
    except (OSError, ValueError):
        pass


def executar_lote(config: AppConfig, linhas: list, audit_root: Path, saida: Path, deps: Dependencias, *,
                  rehearsal: bool, nova_senha_se_ja_cadastrado: bool,
                  entregar: bool = True) -> list[ResultadoAdvogado]:
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
                from .senha_cli import safe_error
                resultados.append(ResultadoAdvogado(linha.numero_linha, linha.nome, "ERROR", safe_error(exc)))
            finally:
                _fechar_caixa(deps, linha.numero_linha)
    except KeyboardInterrupt:
        deps.log("\nInterrompido pelo operador: os advogados ja processados vao para a planilha.")
        if not entregar:
            _resumo(resultados, config, rehearsal, deps.log)
            raise
    finally:
        credenciais = [r.credencial for r in resultados if r.credencial]
        if credenciais and entregar:
            deps.entregar(credenciais, saida)
    _resumo(resultados, config, rehearsal, deps.log)
    return resultados


def _fechar_caixa(deps: Dependencias, numero_linha: int) -> None:
    """Encerra a conexao IMAP do advogado que terminou (a caixa reabre sozinha se for usada de novo):
    nao fica uma conexao aberta por conta durante o lote inteiro."""
    caixa = deps.extras.get("cache", {}).get(numero_linha, {}).get("inbox")
    fechar = getattr(caixa, "close", None)
    if callable(fechar):
        try:
            fechar()
        except Exception:  # noqa: BLE001
            pass


def executar_tribunais(configs: list[AppConfig], linhas: list, audit_root: Path, saida: Path,
                       deps: Dependencias, *, rehearsal: bool,
                       nova_senha_se_ja_cadastrado: bool) -> dict[str, list[ResultadoAdvogado]]:
    """Um tribunal depois do outro (todos os advogados em cada um). Uma planilha de entrega no fim."""
    por_tribunal: dict[str, list[ResultadoAdvogado]] = {}
    try:
        for n, config in enumerate(configs, start=1):
            deps.log(f"\n##################### TRIBUNAL {n}/{len(configs)}: {config.tribunal} #####################")
            por_tribunal[config.tribunal] = executar_lote(
                config, linhas, audit_root, saida, deps, rehearsal=rehearsal,
                nova_senha_se_ja_cadastrado=nova_senha_se_ja_cadastrado, entregar=False)
    except KeyboardInterrupt:
        deps.log("\nExecucao interrompida: os tribunais seguintes nao foram feitos.")
    finally:
        credenciais = [r.credencial for rs in por_tribunal.values() for r in rs if r.credencial]
        if credenciais:
            deps.entregar(credenciais, saida)
    if len(configs) > 1:
        _resumo_geral(por_tribunal, rehearsal, deps.log)
    return por_tribunal


def _resumo_geral(por_tribunal: dict[str, list[ResultadoAdvogado]], rehearsal: bool, log) -> None:
    log(f"\n================ RESUMO GERAL{' (ENSAIO)' if rehearsal else ''} ================")
    for tribunal, resultados in por_tribunal.items():
        if rehearsal:
            ok = sum(1 for r in resultados if r.cadastro == "REHEARSAL_OK")
            log(f"  {tribunal:<24} ensaio OK: {ok} de {len(resultados)}")
        else:
            prontos = sum(1 for r in resultados if r.login_ok)
            log(f"  {tribunal:<24} credenciais prontas: {prontos} de {len(resultados)}")


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


def _conferir_certificados_windows(linhas: list, deps: Dependencias) -> None:
    """Antes de comecar, acha no Windows o A1 de cada linha sem .pfx (pelo CPF ou thumbprint)."""
    from .certificate import CertificateError, find_installed

    pendentes = [l for l in linhas if l.ok and l.usa_windows]
    if not pendentes:
        return
    try:
        if deps.store_factory:
            store = deps.store_factory()
        else:
            from .certificate import WindowsCertificateStore
            store = WindowsCertificateStore()
    except CertificateError as exc:
        for l in pendentes:
            l.erros.append(f"certificado do Windows indisponivel ({exc}); informe o .pfx")
        return
    for l in pendentes:
        try:
            info, aviso = find_installed(store, l.record.cpf_esperado, l.thumbprint or None)
        except CertificateError as exc:
            l.erros.append(str(exc))
            continue
        l.thumbprint = info.thumbprint_sha1  # fixa a escolha para o cadastro usar o mesmo
        l.certificado_info = (f"Windows, {info.emissor}, valido ate {info.valido_ate:%d/%m/%Y}, "
                              f"{info.thumbprint_sha1[:8]}..." + (f" ({aviso})" if aviso else ""))


# ------------------------------------------------------------- assistente
def run_executar(audit_root: Path = Path("runtime/audit"), saida: Path = Path("entregas"),
                 tribunal: str = "", planilha: str = "", ensaio: bool | None = None,
                 deps: Dependencias | None = None, so_senha: bool = False) -> int:
    from .config import load_config
    from .planilha import gravar_modelo, ler_planilha

    deps = deps or _dependencias_padrao()
    log = deps.log
    if so_senha:
        # Advogado ja cadastrado nesses tribunais: nao entra com o certificado (evita bloqueio por
        # excesso de logins) e vai direto para nova senha + teste de login (+ entrega).
        ensaio = False
        deps.extras["so_senha"] = True
        log("SO SENHA: sem login com certificado. Use apenas para tribunais onde o advogado JA tem cadastro.")

    # 1) Tribunal(is)
    opcoes = listar_tribunais()
    if not opcoes:
        log("Nenhum tribunal configurado em tribunais\\")
        return 2
    escolhidos, invalidos = escolher_tribunais(opcoes, tribunal) if tribunal else ([], [])
    if invalidos:
        log("Nao reconhecido(s): " + ", ".join(invalidos))
        escolhidos = []
    while not escolhidos:
        log("\nTribunais disponiveis:")
        for i, o in enumerate(opcoes, start=1):
            log(f"  {i:>2}. {o.chave:<8} {o.nome:<28} {'[envio real liberado]' if o.liberado else '[so ensaio]'}")
        escolhidos, invalidos = escolher_tribunais(opcoes, deps.ask(
            "\nQual(is) tribunal(is)? Um ou varios, separados por virgula (ex.: tremg, tjpa, trt5 ou 8,3,31): "))
        if invalidos:
            log("Nao reconhecido(s): " + ", ".join(invalidos) + ". Tente de novo.")
            escolhidos = []
    log("\nTribunais, nesta ordem:")
    for i, o in enumerate(escolhidos, start=1):
        log(f"  {i}. {o.nome}  {'[envio real liberado]' if o.liberado else '[so ensaio]'}")

    # 2) Modo
    nao_liberados = [o for o in escolhidos if not o.liberado]
    if ensaio is None:
        if len(nao_liberados) == len(escolhidos):
            log("Nenhum destes tribunais tem envio real liberado: sera ENSAIO (entra, confere e preenche, nao envia).")
            ensaio = True
        else:
            ensaio = not deps.yes("Executar em modo REAL (cadastra, assina o termo e cria a senha)?", True)
    if not ensaio and nao_liberados:
        log("Envio real NAO liberado em: " + ", ".join(o.nome for o in nao_liberados))
        log("Para liberar: selectors_approved = true e allow_live_submission = true no .toml do tribunal.")
        if len(nao_liberados) == len(escolhidos) or not deps.yes(
                "Seguir so com os tribunais liberados (os outros ficam de fora)?", True):
            log("Cancelado. Rode em ensaio ou libere os tribunais.")
            return 2
        escolhidos = [o for o in escolhidos if o.liberado]
    configs = [load_config(o.caminho) for o in escolhidos]
    log("Modo: " + ("ENSAIO (nada e enviado nem assinado)" if ensaio else "REAL"))
    for c in configs:
        log(f"  - {c.tribunal}  ({c.base_url})")

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
    _conferir_certificados_windows(linhas, deps)
    validas = [l for l in linhas if l.ok]
    invalidas = [l for l in linhas if not l.ok]
    log(f"\nPlanilha: {caminho}  -> {len(validas)} advogado(s) pronto(s), {len(invalidas)} com erro")
    for l in invalidas:
        log(f"  linha {l.numero_linha} ({l.nome or 'sem nome'}): " + "; ".join(l.erros))
    if not validas:
        log("Nada para executar.")
        return 2
    for l in validas:
        origem = l.certificado_info if l.usa_windows else f"arquivo {l.certificado.name}"
        log(f"  linha {l.numero_linha}: {l.nome}  (OAB {l.record.oab_numero}/{l.record.oab_uf})  certificado: {origem}")

    # 4) Opcoes e confirmacao
    if "assistido" not in deps.extras:
        deps.extras["assistido"] = deps.yes(
            "Modo assistido: se o robo tiver duvida num formulario, pausar para voce resolver no Edge "
            "(em vez de pular o advogado)?", True)
    nova_senha = False
    if so_senha:
        nova_senha = True
    elif not ensaio:
        if deps.extras.get("senha_via_email"):
            # Rota de credencial pelo e-mail: um advogado ja cadastrado sempre precisa de senha nova
            # (e, se o e-mail no PJe divergir, o bot alinha pelo cadastro). Perguntar e redundante.
            nova_senha = True
            log("Advogados ja cadastrados tambem terao nova senha (e o e-mail no PJe sera alinhado se divergir).")
        else:
            nova_senha = deps.yes("Se o advogado JA tiver cadastro em algum destes tribunais, criar uma NOVA senha "
                                  "mesmo assim?", False)
        log("Abra o PJeOffice PRO antes de continuar (ele assina o login e o termo).")
    nomes = ", ".join(c.tribunal for c in configs)
    acao = "ENSAIO" if ensaio else ("NOVA SENHA (sem cadastro)" if so_senha else "CADASTRO REAL")
    if not deps.yes(f"Iniciar {acao} de {len(validas)} advogado(s) "
                    f"em {len(configs)} tribunal(is): {nomes}?", True):
        log("Cancelado.")
        return 1

    audit_root.mkdir(parents=True, exist_ok=True)
    try:
        por_tribunal = executar_tribunais(configs, validas, audit_root, saida, deps, rehearsal=ensaio,
                                          nova_senha_se_ja_cadastrado=nova_senha)
    finally:
        deps.extras.pop("cache", None)  # senhas/chaves pedidas nesta execucao saem da memoria
    resultados = [r for rs in por_tribunal.values() for r in rs]
    esperado = len(validas) * len(configs)
    if ensaio:
        return 0 if len(resultados) == esperado and all(r.cadastro == "REHEARSAL_OK" for r in resultados) else 3
    return 0 if len(resultados) == esperado and all(r.login_ok for r in resultados) else 3
