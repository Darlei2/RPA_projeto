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
    url: str = ""       # endereco de login (define a regra dos dois graus, ver graus.py)


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
                                    cfg.selectors_approved and cfg.allow_live_submission, cfg.base_url))
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
class Opcoes:
    """Como a execucao se comporta (o que o operador escolheu na linha de comando)."""
    so_senha: bool = False          # advogado ja cadastrado: sem login com certificado, so senha + teste
    senha_via_email: bool = False   # pula o login unico (PDPJ): a senha sai pelo "Esqueci minha senha"
    sem_email: bool = False         # se o login unico nao der a senha, nao cai no e-mail (fica pendente)
    assistido: bool | None = None   # duvida no formulario: pausar para o operador (None = perguntar no inicio)
    perguntar_2fa: bool = True      # sem chave 2FA na planilha: perguntar ao operador
    chave_na_planilha: bool = True  # a chave 2FA acompanha a credencial na entrega


@dataclass
class Dependencias:
    """O que a execucao usa de fora. No uso real ficam os padroes; os testes e os fluxos que
    estendem o robo trocam o que precisam aqui, sem substituir funcoes do modulo.

    Pontos de encaixe opcionais:
      ja_feito(config, linha) -> motivo: texto nao vazio = este advogado nao e refeito neste tribunal;
      ler_planilha(caminho, password_provider=...) -> linhas: leitura da planilha de advogados;
      testar_senha(config, audit_root, lawyer, secret_2fa, senha) -> LinhaCredencial: teste do login;
      senha_existente(config, linha) -> senha: o advogado ja tem credencial neste tribunal. O robo so
        confere o login com ela e nao altera o que funciona (ver processar_advogado).
    """
    ask: Callable[[str], str]
    secret: Callable[[str], str]
    yes: Callable[[str, bool], bool]
    cadastrar: Callable[..., object]
    credencial: Callable[..., LinhaCredencial]
    abrir_caixa: Callable[..., object]
    entregar: Callable[[list[LinhaCredencial], Path], None]
    store_factory: Callable[[], object] | None = None
    log: Callable[[str], None] = print
    opcoes: Opcoes = field(default_factory=Opcoes)
    ja_feito: Callable[..., str] | None = None
    ler_planilha: Callable[..., list] | None = None
    testar_senha: Callable[..., LinhaCredencial] | None = None
    senha_existente: Callable[..., str] | None = None
    # Segredos pedidos uma vez por advogado (numero da linha -> dados), so em memoria: apagado no fim.
    cache: dict = field(default_factory=dict)


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
    """Caixa de e-mail do advogado, sempre por IMAP.

    Senha da caixa, nesta ordem: coluna imap_senha; senha de app ja guardada (cofre local); fora
    da rota pelo e-mail, senha de app gerada agora no Gmail com email_senha + email_2fa da planilha.
    Sem senha que funcione: na rota pelo e-mail a conta fica pendente (o robo nao para para
    perguntar); fora dela, o operador informa a caixa na hora.
    """
    from . import cofre_local, senha_cli
    from .email_inbox import ImapInbox, guess_imap_host

    gerada = False
    if deps.opcoes.senha_via_email:
        # Rota de e-mail com IMAP cadastrado a mao: usa a imap_senha da planilha ou o cofre e
        # nao gera senha de app.
        email_senha = email_2fa = ""
    if not password:
        password = cofre_local.ler(email)
    if not password and email_senha and email_2fa:
        password, gerada = _senha_de_app_gmail(email, email_senha, email_2fa, deps), True
    if not password:
        return _sem_imap(email, deps, "sem senha IMAP para esta conta")
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
        return _sem_imap(email, deps, "a caixa recusou a senha IMAP")


@dataclass
class CaixaPendente:
    """Conta sem IMAP utilizavel na rota pelo e-mail: fica pendente e o PJe nao recebe o pedido."""
    motivo: str


def _sem_imap(email: str, deps: Dependencias, por_que: str):
    """Sem IMAP utilizavel: na rota pelo e-mail a conta fica pendente (o robo nao para para pedir
    senha ao operador); fora dela, o operador informa a caixa."""
    from . import senha_cli

    if deps.opcoes.senha_via_email:
        return CaixaPendente(por_que)
    return senha_cli._setup_inbox(email)


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
        return gerar_senha_de_app(email, senha, chave, assist=assist if deps.opcoes.assistido else None)
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
    return _ask_2fa_key() if deps.opcoes.perguntar_2fa else ""


def processar_advogado(config: AppConfig, linha, audit_root: Path, deps: Dependencias, *,
                       rehearsal: bool, nova_senha_se_ja_cadastrado: bool) -> ResultadoAdvogado:
    """Um advogado num tribunal. Primeiro, o que o tribunal exige da planilha (exigencias.py): linha
    sem o dado exigido e pulada antes de qualquer acesso, inclusive no ensaio. Depois, tres regras
    antes do fluxo normal (nenhuma vale no ensaio):

    1. deps.ja_feito pode dizer que este advogado nao precisa ser refeito neste tribunal.
    2. Tribunal com um cadastro por grau e uma credencial so (graus.py): o segundo grau usa a
       senha do primeiro e nao e feito se o primeiro ficou sem credencial pronta.
    3. deps.senha_existente (o advogado ja tem credencial): o robo testa o login com essa senha,
       sem certificado. Se entra, nada e alterado. Se nao entra, o tribunal so e completado
       (cadastro + a MESMA senha) quando essa senha ja foi confirmada no outro grau do mesmo
       tribunal; senao nada e alterado, porque trocar a senha quebraria a credencial que existe."""
    from . import exigencias, graus

    def cabecalho() -> None:
        deps.log(f"\n========== Linha {linha.numero_linha}: {linha.nome} ({config.tribunal}) ==========")

    def um(senha_do_outro_grau: str = "", mostrar_cabecalho: bool = True) -> ResultadoAdvogado:
        return _processar_um(config, linha, audit_root, deps, rehearsal=rehearsal,
                             nova_senha_se_ja_cadastrado=nova_senha_se_ja_cadastrado,
                             senha_do_outro_grau=senha_do_outro_grau, mostrar_cabecalho=mostrar_cabecalho)

    falta = exigencias.faltando(config.base_url, linha.record)
    if falta:
        cabecalho()
        motivo = (f"este tribunal exige {' e '.join(falta)} no cadastro e a planilha esta sem. "
                  "Nada foi acessado: preencha e rode de novo.")
        deps.log("PULADO: " + motivo)
        return ResultadoAdvogado(linha.numero_linha, linha.nome, "PULADO", motivo)
    if rehearsal:
        return um()
    if deps.ja_feito:
        motivo = deps.ja_feito(config, linha)
        if motivo:
            cabecalho()
            deps.log(motivo)
            return ResultadoAdvogado(linha.numero_linha, linha.nome, "JA_FEITO", motivo)
    nome_grupo = graus.grupo(config.base_url)
    feitos = deps.cache.setdefault(linha.numero_linha, {}).setdefault("graus", {})
    anterior = feitos.get(nome_grupo) if nome_grupo else None
    if anterior is not None and not anterior["senha"]:
        cabecalho()
        motivo = (f"{anterior['tribunal']} ficou sem credencial pronta nesta execucao; os dois graus do "
                  f"{nome_grupo} precisam da mesma senha. Rode de novo para refazer os dois.")
        deps.log("PULADO: " + motivo)
        return ResultadoAdvogado(linha.numero_linha, linha.nome, "PULADO", motivo)

    existente = (deps.senha_existente(config, linha) or "") if deps.senha_existente else ""
    if existente:
        cabecalho()
        conferido = _conferir_acesso(config, linha, audit_root, deps, existente)
        if conferido.login_ok:
            if nome_grupo and anterior is None:
                feitos[nome_grupo] = {"tribunal": config.tribunal, "senha": existente}
            return conferido
        if not (anterior and anterior["senha"] == existente):
            conferido.cadastro = "SENHA_NAO_CONFERE"
            conferido.motivo = ("a senha da credencial que ja existe nao entra neste tribunal (" + conferido.motivo
                                + "). Nada foi alterado: trocar a senha aqui quebraria essa credencial.")
            deps.log("NADA ALTERADO: " + conferido.motivo)
            if nome_grupo:
                feitos.setdefault(nome_grupo, {"tribunal": config.tribunal, "senha": ""})
            return conferido
        deps.log(f"  o login nao entrou aqui; a senha ja foi confirmada no {anterior['tribunal']}: "
                 "este grau sera completado (cadastro + a mesma senha)")
    try:
        res = um(anterior["senha"] if anterior else "", mostrar_cabecalho=not existente)
    except Exception:
        if nome_grupo:
            feitos.setdefault(nome_grupo, {"tribunal": config.tribunal, "senha": ""})
        raise
    if not nome_grupo:
        return res
    senha = res.credencial.senha if res.login_ok else ""
    if anterior is None:
        feitos[nome_grupo] = {"tribunal": config.tribunal, "senha": senha}
    elif senha and senha != anterior["senha"]:
        aviso = f"senha DIFERENTE da do {anterior['tribunal']}: a credencial unica do {nome_grupo} nao vale"
        res.credencial.observacao = (res.credencial.observacao + " | " if res.credencial.observacao else "") + aviso
        res.motivo = (res.motivo + " | " if res.motivo else "") + aviso
        deps.log("ATENCAO: " + aviso + ". Rode de novo para refazer os dois graus.")
    return res


def _conferir_acesso(config: AppConfig, linha, audit_root: Path, deps: Dependencias, senha: str) -> ResultadoAdvogado:
    """Advogado que ja tem credencial: testa CPF + senha + 2FA, sem certificado e sem alterar nada.
    Login OK: devolve o resultado com a credencial conferida. Login falho: resultado sem credencial
    (a senha existente nao vai para a entrega)."""
    record = linha.record
    cache = deps.cache.setdefault(linha.numero_linha, {})
    if "secret_2fa" not in cache:
        cache["secret_2fa"] = _chave_2fa(linha, deps)
    secret_2fa = cache["secret_2fa"]
    deps.log("Credencial ja existente: conferindo o login com CPF + senha + 2FA (sem certificado)")
    testar = deps.testar_senha
    if testar is None:
        from .senha_cli import testar_credencial as testar
    lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
              "chave_2fa_exibir": secret_2fa if deps.opcoes.chave_na_planilha else ""}
    credencial = testar(config, audit_root, lawyer, secret_2fa, senha)
    if credencial.login_testado:
        deps.log("OK: o login ja funciona neste tribunal. Nada foi alterado.")
        return ResultadoAdvogado(linha.numero_linha, linha.nome, "JA_TEM_ACESSO",
                                 "login conferido com a senha ja existente; nada foi alterado", credencial)
    return ResultadoAdvogado(linha.numero_linha, linha.nome, "SEM_ACESSO",
                             credencial.observacao or "o login nao entrou")


def _processar_um(config: AppConfig, linha, audit_root: Path, deps: Dependencias, *,
                  rehearsal: bool, nova_senha_se_ja_cadastrado: bool,
                  senha_do_outro_grau: str = "", mostrar_cabecalho: bool = True) -> ResultadoAdvogado:
    """Cadastro com o certificado e, se couber, senha + teste de login. senha_do_outro_grau: a senha
    que ja ficou no outro grau do mesmo tribunal (graus.py) e tem de ser repetida aqui."""
    res = ResultadoAdvogado(linha.numero_linha, linha.nome)
    record = linha.record
    if mostrar_cabecalho:
        deps.log(f"\n========== Linha {linha.numero_linha}: {linha.nome} ({config.tribunal}) ==========")

    # Segredos pedidos uma vez por advogado e reaproveitados nos outros tribunais da execucao
    # (so em memoria; o cache e apagado no fim de run_executar).
    cache = deps.cache.setdefault(linha.numero_linha, {})
    if linha.usa_windows or deps.opcoes.so_senha:
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

    if deps.opcoes.so_senha:
        res.cadastro, res.motivo = "NAO_TENTADO", "opcao so senha: cadastro nao conferido"
        deps.log("1/2 Cadastro: pulado (opcao so senha, sem login com certificado)")
        return _etapa_senha(config, linha, audit_root, deps, res, cache, secret_2fa, senha_do_outro_grau)

    # 1) Cadastro com o certificado
    deps.log("1/2 Cadastro com o certificado A1" + (" (instalado no Windows)" if linha.usa_windows else " (.pfx)"))
    kwargs = {"rehearsal": rehearsal, "notify": deps.log, "thumbprint": linha.thumbprint}
    if not rehearsal and not deps.opcoes.senha_via_email:
        # Senha pelo login unico (PDPJ), sem e-mail, na mesma sessao do certificado; se o tribunal
        # nao oferecer, o robo cai no caminho do e-mail ("Esqueci minha senha").
        # Com a opcao senha_via_email, pula o login unico e vai direto pelo e-mail (IMAP da planilha).
        kwargs["senha_sso"] = True
        kwargs["senha_sso_ja_cadastrado"] = nova_senha_se_ja_cadastrado
        if senha_do_outro_grau:
            kwargs["senha_preferida"] = senha_do_outro_grau
    if deps.opcoes.assistido:
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
        testar = deps.testar_senha
        if testar is None:
            from .senha_cli import testar_credencial as testar
        lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
                  "chave_2fa_exibir": secret_2fa if deps.opcoes.chave_na_planilha else ""}
        res.credencial = testar(config, audit_root, lawyer, secret_2fa, senha_sso)
        senha_sso = ""
        if res.credencial.login_testado:
            return res
        deps.log("  a senha do login unico nao passou no teste de login")
    if deps.opcoes.sem_email:
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
    return _etapa_senha(config, linha, audit_root, deps, res, cache, secret_2fa, senha_do_outro_grau)


def _etapa_senha(config, linha, audit_root: Path, deps: Dependencias, res, cache: dict, secret_2fa: str,
                 senha_preferida: str = ""):
    """2) Senha: pedido do link, e-mail, nova senha e teste do login. senha_preferida: a senha do
    outro grau do mesmo tribunal, que tem de ser repetida (graus.py)."""
    record = linha.record
    deps.log("2/2 Credencial: nova senha pelo e-mail e teste de CPF + senha + 2FA")
    if "inbox" not in cache:
        cache["inbox"] = deps.abrir_caixa(record.email, linha.segredo("imap_servidor"),
                                          linha.segredo("imap_usuario"), linha.segredo("imap_senha"), deps,
                                          linha.segredo("email_senha"), linha.segredo("email_2fa"))
    inbox = cache["inbox"]
    if isinstance(inbox, CaixaPendente):
        # Sem como ler o e-mail desta conta: nao pede senha nova ao PJe (o link chegaria e se perderia).
        deps.log("PENDENTE: caixa de e-mail sem acesso (" + inbox.motivo + "). Pedido de senha NAO enviado ao PJe.")
        res.credencial = LinhaCredencial(config.tribunal, config.base_url, record.nome_esperado,
                                         record.cpf_esperado, record.email,
                                         observacao="PENDENTE - e-mail: " + inbox.motivo)
        return res
    lawyer = {"nome": record.nome_esperado, "cpf": record.cpf_esperado, "email": record.email,
              "chave_2fa_exibir": secret_2fa if deps.opcoes.chave_na_planilha else ""}
    if senha_preferida:
        lawyer["senha_preferida"] = senha_preferida
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
    caixa = deps.cache.get(numero_linha, {}).get("inbox")
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
def _escolher(opcoes: list[OpcaoTribunal], tribunal: str, deps: Dependencias) -> list[OpcaoTribunal]:
    """1) Tribunal(is): os da linha de comando ou os que o operador responder, ja com a regra dos
    dois graus aplicada (graus.py). Lista vazia = nada para executar."""
    from . import graus

    log = deps.log
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
    escolhidos, incluidos, incompletos = graus.completar(escolhidos, opcoes)
    for nome in incluidos:
        log(f"Regra dos dois graus: incluido {nome} (cadastro e senha sao feitos nos dois graus).")
    for nome_grupo in incompletos:
        log(f"Regra dos dois graus: {nome_grupo} ficou de fora, falta o arquivo do outro grau em tribunais\\")
    if not escolhidos:
        log("Nenhum tribunal para executar.")
        return []
    log("\nTribunais, nesta ordem:")
    for i, o in enumerate(escolhidos, start=1):
        log(f"  {i}. {o.nome}  {'[envio real liberado]' if o.liberado else '[so ensaio]'}")
    return escolhidos


def _definir_modo(escolhidos: list[OpcaoTribunal], ensaio: bool | None,
                  deps: Dependencias) -> tuple[list[OpcaoTribunal], bool]:
    """2) Ensaio ou real. No real ficam so os tribunais com envio liberado (e, na regra dos dois
    graus, so os que tem os dois liberados). Lista vazia = cancelado."""
    from . import graus

    log = deps.log
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
            return [], ensaio
        escolhidos, fora = graus.so_grupos_inteiros([o for o in escolhidos if o.liberado])
        for nome_grupo in fora:
            log(f"Regra dos dois graus: {nome_grupo} ficou de fora, so um dos graus tem envio real liberado.")
        if not escolhidos:
            log("Cancelado. Libere os dois graus ou rode em ensaio.")
    return escolhidos, ensaio


def _ler_advogados(planilha: str, deps: Dependencias) -> tuple[list, int | None]:
    """3) Planilha: pergunta o caminho se preciso, le e mostra quem esta pronto.
    Devolve (advogados prontos, None) ou ([], codigo de saida) quando nao ha o que executar."""
    from .planilha import gravar_modelo, ler_planilha

    log = deps.log
    caminho = Path(planilha) if planilha else None
    while caminho is None or not caminho.exists():
        if caminho is not None:
            log(f"Planilha nao encontrada: {caminho}")
            if not caminho.exists() and caminho == PLANILHA_PADRAO and deps.yes(
                    f"Criar a planilha modelo em {PLANILHA_PADRAO} para voce preencher?", True):
                gravar_modelo(PLANILHA_PADRAO)
                log(f"Modelo criado: {PLANILHA_PADRAO}. Preencha (uma linha por advogado) e rode de novo.")
                return [], 0
        resposta = deps.ask(f"Planilha dos advogados [{PLANILHA_PADRAO}]: ").strip().strip('"')
        caminho = Path(resposta) if resposta else PLANILHA_PADRAO
    ler = deps.ler_planilha or ler_planilha
    linhas = ler(caminho, password_provider=lambda: deps.secret("Senha para abrir a planilha de advogados"))
    _conferir_certificados_windows(linhas, deps)
    validas = [l for l in linhas if l.ok]
    invalidas = [l for l in linhas if not l.ok]
    log(f"\nPlanilha: {caminho}  -> {len(validas)} advogado(s) pronto(s), {len(invalidas)} com erro")
    for l in invalidas:
        log(f"  linha {l.numero_linha} ({l.nome or 'sem nome'}): " + "; ".join(l.erros))
    if not validas:
        log("Nada para executar.")
        return [], 2
    for l in validas:
        origem = l.certificado_info if l.usa_windows else f"arquivo {l.certificado.name}"
        log(f"  linha {l.numero_linha}: {l.nome}  (OAB {l.record.oab_numero}/{l.record.oab_uf})  certificado: {origem}")
    return validas, None


def _confirmar(configs: list[AppConfig], validas: list, ensaio: bool, deps: Dependencias) -> bool | None:
    """4) Opcoes que faltam e a confirmacao final. Devolve se advogado ja cadastrado tambem ganha
    senha nova, ou None se o operador cancelou."""
    log, opcoes = deps.log, deps.opcoes
    if opcoes.assistido is None:
        opcoes.assistido = deps.yes(
            "Modo assistido: se o robo tiver duvida num formulario, pausar para voce resolver no Edge "
            "(em vez de pular o advogado)?", True)
    nova_senha = False
    if opcoes.so_senha:
        nova_senha = True
    elif not ensaio:
        if opcoes.senha_via_email:
            # Rota de credencial pelo e-mail: um advogado ja cadastrado sempre precisa de senha nova
            # (e, se o e-mail no PJe divergir, o bot alinha pelo cadastro). Perguntar e redundante.
            nova_senha = True
            log("Advogados ja cadastrados tambem terao nova senha (e o e-mail no PJe sera alinhado se divergir).")
        else:
            nova_senha = deps.yes("Se o advogado JA tiver cadastro em algum destes tribunais, criar uma NOVA senha "
                                  "mesmo assim?", False)
        log("Abra o PJeOffice PRO antes de continuar (ele assina o login e o termo).")
    nomes = ", ".join(c.tribunal for c in configs)
    acao = ("ENSAIO" if ensaio else "NOVA SENHA (sem cadastro)" if opcoes.so_senha
            else "CONFERENCIA DA CREDENCIAL EXISTENTE (so completa o que falta)" if deps.senha_existente
            else "CADASTRO REAL")
    if not deps.yes(f"Iniciar {acao} de {len(validas)} advogado(s) "
                    f"em {len(configs)} tribunal(is): {nomes}?", True):
        return None
    return nova_senha


def run_executar(audit_root: Path = Path("runtime/audit"), saida: Path = Path("entregas"),
                 tribunal: str = "", planilha: str = "", ensaio: bool | None = None,
                 deps: Dependencias | None = None, so_senha: bool = False) -> int:
    """Execucao geral: tribunal(is), modo, planilha, confirmacao e o lote. Codigo de saida:
    0 = tudo certo; 1 = cancelado pelo operador; 2 = nada para executar; 3 = algum advogado ficou sem
    credencial pronta (ou sem ensaio OK)."""
    from .config import load_config

    deps = deps or _dependencias_padrao()
    log = deps.log
    if so_senha:
        deps.opcoes.so_senha = True
    if deps.opcoes.so_senha:
        # Advogado ja cadastrado nesses tribunais: nao entra com o certificado (evita bloqueio por
        # excesso de logins) e vai direto para nova senha + teste de login (+ entrega).
        ensaio = False
        log("SO SENHA: sem login com certificado. Use apenas para tribunais onde o advogado JA tem cadastro.")

    opcoes = listar_tribunais()
    if not opcoes:
        log("Nenhum tribunal configurado em tribunais\\")
        return 2
    escolhidos = _escolher(opcoes, tribunal, deps)
    if not escolhidos:
        return 2
    escolhidos, ensaio = _definir_modo(escolhidos, ensaio, deps)
    if not escolhidos:
        return 2
    configs = [load_config(o.caminho) for o in escolhidos]
    log("Modo: " + ("ENSAIO (nada e enviado nem assinado)" if ensaio else "REAL"))
    for c in configs:
        log(f"  - {c.tribunal}  ({c.base_url})")

    validas, codigo = _ler_advogados(planilha, deps)
    if codigo is not None:
        return codigo
    from . import exigencias

    for l in validas:
        for c in configs:
            falta = exigencias.faltando(c.base_url, l.record)
            if falta:
                log(f"  ATENCAO linha {l.numero_linha}: sem {' e '.join(falta)}, exigido em {c.tribunal}. "
                    "A linha sera pulada nesse tribunal.")
    nova_senha = _confirmar(configs, validas, ensaio, deps)
    if nova_senha is None:
        log("Cancelado.")
        return 1

    audit_root.mkdir(parents=True, exist_ok=True)
    try:
        por_tribunal = executar_tribunais(configs, validas, audit_root, saida, deps, rehearsal=ensaio,
                                          nova_senha_se_ja_cadastrado=nova_senha)
    finally:
        deps.cache.clear()  # senhas/chaves pedidas nesta execucao saem da memoria
    resultados = [r for rs in por_tribunal.values() for r in rs]
    esperado = len(validas) * len(configs)
    if ensaio:
        return 0 if len(resultados) == esperado and all(r.cadastro == "REHEARSAL_OK" for r in resultados) else 3
    return 0 if len(resultados) == esperado and all(r.login_ok for r in resultados) else 3
