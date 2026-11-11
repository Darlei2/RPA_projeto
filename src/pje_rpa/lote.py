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

    # Segredos pedidos uma vez por advogado e reaproveitados nos outros tribunais da execucao
    # (so em memoria; o cache e apagado no fim de run_executar).
    cache = deps.extras.setdefault("cache", {}).setdefault(linha.numero_linha, {})
    if linha.usa_windows:
        pfx, pfx_password = None, ""  # certificado ja instalado: sem arquivo e sem senha
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

    # 1) Cadastro com o certificado
    deps.log("1/2 Cadastro com o certificado A1" + (" (instalado no Windows)" if linha.usa_windows else " (.pfx)"))
    kwargs = {"rehearsal": rehearsal, "notify": deps.log, "thumbprint": linha.thumbprint}
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

    # 2) Senha: pedido do link, e-mail, nova senha e teste do login
    deps.log("2/2 Credencial: nova senha pelo e-mail e teste de CPF + senha + 2FA")
    if "inbox" not in cache:
        cache["inbox"] = deps.abrir_caixa(record.email, linha.segredo("imap_servidor"),
                                          linha.segredo("imap_usuario"), linha.segredo("imap_senha"), deps)
    inbox = cache["inbox"]
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
                resultados.append(ResultadoAdvogado(linha.numero_linha, linha.nome, "ERROR",
                                                    f"{type(exc).__name__}: {exc}"))
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
                 deps: Dependencias | None = None) -> int:
    from .config import load_config
    from .planilha import gravar_modelo, ler_planilha

    deps = deps or _dependencias_padrao()
    log = deps.log

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
    if not ensaio:
        nova_senha = deps.yes("Se o advogado JA tiver cadastro em algum destes tribunais, criar uma NOVA senha "
                              "mesmo assim?", False)
        log("Abra o PJeOffice PRO antes de continuar (ele assina o login e o termo).")
    nomes = ", ".join(c.tribunal for c in configs)
    if not deps.yes(f"Iniciar {'ENSAIO' if ensaio else 'CADASTRO REAL'} de {len(validas)} advogado(s) "
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
