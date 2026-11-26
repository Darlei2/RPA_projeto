from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from .models import Address, AppConfig, BrowserConfig, LawyerRecord, digits
from .signer import SignerConfig


class ConfigurationError(ValueError):
    pass


def _read_toml(path: Path, chain: tuple[Path, ...] = ()) -> dict:
    """Le o .toml e aplica `herda = "arquivo.toml"` (caminho relativo ao proprio arquivo).

    O arquivo herdado fornece a base; cada secao do arquivo do tribunal sobrescreve
    somente as chaves que declara (ex.: so `login_certificate_button` em [selectors]).
    """
    path = path.resolve()
    if path in chain:
        raise ConfigurationError(f"Heranca circular em {path.name}")
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    parent = raw.pop("herda", None)
    if not parent:
        return raw
    base = _read_toml(path.parent / str(parent), chain + (path,))
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def _secao_tribunal(path: Path) -> dict:
    """Secao [tribunal] do PROPRIO arquivo (identidade nao e herdada por `herda`)."""
    with path.open("rb") as stream:
        proprio = tomllib.load(stream).get("tribunal", {})
    if not isinstance(proprio, dict):
        raise ConfigurationError(f"{path.name}: [tribunal] deve ser uma secao")
    return proprio


def resolver_identidade(path: Path, app: dict):
    """Identidade do tribunal do arquivo: registro da pasta (tribunais\\_registro.toml) e/ou secao
    [tribunal] do proprio arquivo. NUNCA a partir do endereco. Devolve (Tribunal, origem, avisos).
    Conflito ou tribunal desconhecido com registro presente: ConfigurationError explicito."""
    from . import registro

    path = Path(path)
    if path.stem.startswith("_"):  # base comum (_comum.toml): nao e um tribunal
        return registro.Tribunal(id="", nome=str(app.get("tribunal", "")), ramo=""), "base", []
    try:
        reg = registro.carregar(path.parent)
    except registro.RegistroInvalido as exc:
        raise ConfigurationError(str(exc)) from exc
    proprio = _secao_tribunal(path)
    tid = str(proprio.get("id", path.stem))
    if tid != path.stem:
        raise ConfigurationError(f"{path.name}: [tribunal] id = '{tid}' tem de ser igual ao nome do arquivo "
                                 f"('{path.stem}'): o id e permanente e e por ele que pedidos e credenciais "
                                 "reconhecem o tribunal")
    nome_app = str(app.get("tribunal", "")).strip()
    entrada = reg.get(tid) if reg else None
    avisos: list[str] = []
    if entrada and proprio:
        for campo, valor in proprio.items():
            atual = getattr(entrada, campo, None)
            if campo == "hosts_autorizados":
                atual, valor = list(atual or ()), [str(h).lower() for h in (valor or [])]
            if campo != "id" and valor != atual:
                raise ConfigurationError(
                    f"{path.name}: [tribunal] {campo} = {valor!r} diverge do registro ({atual!r}). "
                    f"A identidade fica em {registro.ARQUIVO}: corrija um dos dois.")
        origem = "registro"
    elif entrada:
        origem = "registro"
    elif proprio:
        dados = {"nome": nome_app, **proprio, "id": tid}
        try:
            entrada = registro.tribunal_de_dict(dados, path.name)
        except registro.RegistroInvalido as exc:
            raise ConfigurationError(str(exc)) from exc
        origem = "arquivo"
    elif reg is not None:
        raise ConfigurationError(
            f"{path.name}: o tribunal '{tid}' nao esta no registro ({registro.ARQUIVO}). Acrescente-o ao "
            "registro (id, nome, ramo, grau, grupo_credencial, hosts_autorizados) ou declare a identidade "
            "numa secao [tribunal] do proprio arquivo.")
    else:
        entrada = registro.Tribunal(id=tid, nome=nome_app or "NAO_CONFIGURADO", ramo="")
        origem = "sem-registro"
    grau_app = str(app.get("grau", "")).strip().upper()
    if grau_app and origem != "base" and grau_app != entrada.grau:
        raise ConfigurationError(f"{path.name}: app.grau = '{grau_app}' diverge da identidade do tribunal "
                                 f"('{entrada.grau or 'instancia unica'}'). O grau fica no registro: tire app.grau.")
    if str(app.get("domain", "")).strip():
        avisos.append(f"{path.name}: app.domain e ignorado: o dominio de integracao e ligado ao id do tribunal "
                      "fora do .toml")
    if origem != "sem-registro" and nome_app and nome_app != entrada.nome:
        avisos.append(f"{path.name}: app.tribunal = '{nome_app}' difere do nome do registro "
                      f"('{entrada.nome}'); vale o do registro")
    return entrada, origem, avisos


def load_config(path: Path) -> AppConfig:
    raw = _read_toml(path)
    raw.pop("tribunal", None)  # a identidade vem de resolver_identidade (sem heranca)

    app = raw.get("app", {})
    browser = raw.get("browser", {})
    base_url = str(app.get("base_url", "")).strip()
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigurationError("app.base_url deve ser uma URL HTTPS valida")

    selectors = _string_lists(raw.get("selectors", {}), "selectors")
    stop_markers = _string_lists(raw.get("stop_markers", {}), "stop_markers")
    required = {
        "login_certificate_button",
        "registration_form",
        "detected_name",
        "detected_cpf",
        "detected_oab",
        "email",
        "phone",
        "cep",
        "logradouro",
        "address_number",
        "bairro",
        "cidade",
        "uf",
        "confirm_data",
        "commitment_term",
        "sign_term",
        "completion",
        "lawyer_panel",
    }
    missing = sorted(required - selectors.keys())
    if missing:
        raise ConfigurationError(f"Seletores obrigatorios ausentes: {', '.join(missing)}")
    tribunal, origem, avisos = resolver_identidade(Path(path), app)
    aprovado = app.get("endereco_aprovado", "")
    if not isinstance(aprovado, str):
        raise ConfigurationError("app.endereco_aprovado deve ser texto")

    return AppConfig(
        environment=str(app.get("environment", "simulation")),
        tribunal=(tribunal.nome if origem in ("registro", "arquivo")
                  else str(app.get("tribunal", "NAO_CONFIGURADO"))),
        base_url=base_url,
        selectors_approved=bool(app.get("selectors_approved", False)),
        allow_live_submission=bool(app.get("allow_live_submission", False)),
        browser=BrowserConfig(
            engine=str(browser.get("engine", "chromium")),
            channel=str(browser.get("channel", "msedge")),
            headless=bool(browser.get("headless", False)),
            timeout_ms=int(browser.get("timeout_ms", 30000)),
        ),
        selectors=selectors,
        stop_markers=stop_markers,
        signer=_signer_config(raw.get("signer", {})),
        # chave antiga dos .toml: aceita, mas sem efeito (a referencia deixou de ser exigida)
        require_authorization_ref=bool(app.get("require_authorization_ref", False)),
        tribunal_id=tribunal.id,
        grau=tribunal.grau,
        grupo_credencial=tribunal.grupo_credencial,
        ramo=tribunal.ramo,
        identidade_origem=origem,
        hosts_autorizados=tuple(tribunal.hosts_autorizados),
        endereco_padrao=tribunal.endereco_padrao,
        endereco_aprovado=aprovado.strip(),
        avisos=tuple(avisos),
    )


def _signer_config(raw: dict) -> SignerConfig:
    defaults = SignerConfig()
    return SignerConfig(
        window_title_regex=str(raw.get("window_title_regex", defaults.window_title_regex)),
        appear_timeout_s=int(raw.get("appear_timeout_s", defaults.appear_timeout_s)),
        max_dialogs=int(raw.get("max_dialogs", defaults.max_dialogs)),
        jab_dll=str(raw.get("jab_dll", defaults.jab_dll)),
        store_option=str(raw.get("store_option", defaults.store_option)),
        ok_button=str(raw.get("ok_button", defaults.ok_button)),
    )


def load_lawyer(path: Path) -> LawyerRecord:
    return lawyer_from_dict(json.loads(path.read_text(encoding="utf-8")))


def lawyer_from_dict(raw: dict) -> LawyerRecord:
    address = raw.get("endereco", {})
    record = LawyerRecord(
        nome_esperado=str(raw.get("nome_esperado", "")).strip(),
        cpf_esperado=digits(str(raw.get("cpf_esperado", ""))),
        oab_numero=digits(str(raw.get("oab_numero", ""))),
        oab_uf=str(raw.get("oab_uf", "")).strip().upper(),
        email=str(raw.get("email", "")).strip(),
        telefone=digits(str(raw.get("telefone", ""))),
        endereco=Address(
            cep=digits(str(address.get("cep", ""))),
            logradouro=str(address.get("logradouro", "")).strip(),
            numero=str(address.get("numero", "")).strip(),
            complemento=str(address.get("complemento", "")).strip(),
            bairro=str(address.get("bairro", "")).strip(),
            cidade=str(address.get("cidade", "")).strip(),
            uf=str(address.get("uf", "")).strip().upper(),
        ),
        autorizacao_ref=str(raw.get("autorizacao_ref", "")).strip(),
        rg_numero=str(raw.get("rg_numero", "")).strip(),
        rg_orgao=str(raw.get("rg_orgao", "")).strip().upper(),
        rg_data_expedicao=str(raw.get("rg_data_expedicao", "")).strip(),
        estado_civil=str(raw.get("estado_civil", "")).strip(),
    )
    errors = validate_lawyer(record)
    if errors:
        raise ConfigurationError("; ".join(errors))
    return record


def validate_lawyer(record: LawyerRecord) -> list[str]:
    errors: list[str] = []
    if len(record.nome_esperado) < 5:
        errors.append("nome_esperado invalido")
    if len(record.cpf_esperado) != 11 or not _valid_cpf(record.cpf_esperado):
        errors.append("cpf_esperado invalido")
    if not record.oab_numero:
        errors.append("oab_numero obrigatorio")
    if not re.fullmatch(r"[A-Z]{2}", record.oab_uf):
        errors.append("oab_uf invalida")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", record.email):
        errors.append("email invalido")
    # Telefone opcional na planilha: so e exigido nos tribunais listados em exigencias.py (TRTs).
    # Nos demais, sem ele o robo mantem o que o PJe ja mostra e so para se o campo for obrigatorio
    # e estiver vazio.
    if record.telefone and not 10 <= len(record.telefone) <= 11:
        errors.append("telefone invalido")
    if len(record.endereco.cep) != 8:
        errors.append("CEP invalido")
    for name in ("logradouro", "numero", "bairro", "cidade"):
        if not getattr(record.endereco, name):
            errors.append(f"endereco.{name} obrigatorio")
    if not re.fullmatch(r"[A-Z]{2}", record.endereco.uf):
        errors.append("endereco.uf invalida")
    return errors


def assert_live_ready(config: AppConfig, record: LawyerRecord | None = None) -> None:
    if config.environment != "production":
        raise ConfigurationError("Modo live exige app.environment = 'production'")
    if not config.selectors_approved:
        raise ConfigurationError("Seletores ainda nao foram homologados")
    if not config.allow_live_submission:
        raise ConfigurationError("Envio real permanece bloqueado por configuracao")
    _assert_official_target(config, record, envio_real=True)


def assert_rehearsal_ready(config: AppConfig, record: LawyerRecord | None = None) -> None:
    """Ensaio usa o PJe real, mas nao envia nada: dispensa a homologacao dos seletores e a
    chave de envio, que e justamente o que o ensaio serve para validar."""
    if config.environment != "production":
        raise ConfigurationError("Ensaio exige app.environment = 'production'")
    _assert_official_target(config, record)


def _assert_official_target(config: AppConfig, record: LawyerRecord | None, envio_real: bool = False) -> None:
    """Validacao dos enderecos (enderecos.py), independente da identidade do tribunal."""
    from . import enderecos

    if "exemplo" in config.base_url.lower() or "example" in config.base_url.lower():
        raise ConfigurationError("Substitua a URL de exemplo pela URL oficial do tribunal")
    erros = enderecos.problemas(config)
    if erros:
        raise ConfigurationError(f"Endereco de login de {config.tribunal_id or config.tribunal} recusado: "
                                 + "; ".join(erros))
    # autorizacao_ref e opcional: os contratos/autorizacoes ficam na base do operador. Quando
    # preenchida, continua registrada na auditoria de cada execucao.
    if record and ("TESTE" in record.nome_esperado.upper() or record.email.endswith("@example.com")):
        raise ConfigurationError("Substitua os dados de exemplo antes do modo live")
    hostname = (urlparse(config.base_url).hostname or "").lower()
    if not hostname.endswith(".jus.br"):
        raise ConfigurationError("Modo live aceita somente dominio oficial terminado em .jus.br")
    if envio_real and not enderecos.aprovado_para_envio_real(config):
        raise ConfigurationError(enderecos.pendencia_do_envio_real(config))


def _string_lists(value: object, label: str) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"Secao [{label}] invalida")
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x for x in items):
            raise ConfigurationError(f"{label}.{key} deve ser uma lista nao vazia de textos")
        result[str(key)] = list(items)
    return result


def _valid_cpf(number: str) -> bool:
    if len(number) != 11 or number == number[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(number[i]) * (size + 1 - i) for i in range(size))
        digit = (total * 10 % 11) % 10
        if digit != int(number[size]):
            return False
    return True
