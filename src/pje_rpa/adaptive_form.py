"""Motor adaptativo para formularios PJe.

Inspeciona controles, valida identidade e preenche os campos reconhecidos.
`operate` nao envia formularios. `locate_action`/`click_action` localizam o botao de
avancar/assinar de cada PJe pelo texto; quem decide clicar e o fluxo (nunca no ensaio).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


JS_SNAPSHOT = r"""() => {
  const visible = e => !!(e.getClientRects().length) &&
    getComputedStyle(e).visibility !== 'hidden';

  const txt = e => (e?.innerText || e?.textContent || '')
    .trim().replace(/\s+/g, ' ');

  const all = [...document.querySelectorAll(
    'input,select,textarea'
  )];

  const controls = all.map((e, index) => {
    if (!visible(e) ||
        /^(hidden|submit|reset|button|image|file|password)$/i
          .test(e.type || '')) return null;

    const labels = e.labels
      ? [...e.labels].map(txt).join(' ')
      : '';

    const labelledBy = e.getAttribute('aria-labelledby');

    const aria = [
      e.getAttribute('aria-label') || '',
      labelledBy
        ? labelledBy.split(/\s+/)
            .map(id => txt(document.getElementById(id)))
            .join(' ')
        : ''
    ].filter(Boolean).join(' ');

    const td = e.closest('td');
    const prev = td?.previousElementSibling;

    const rowLabel =
      prev?.tagName === 'TD' || prev?.tagName === 'TH'
        ? txt(prev).slice(0, 100)
        : '';

    const parentLabel = txt(
      e.parentElement?.querySelector('label') || null
    ).slice(0, 100);

    // Bloco da tela (ex.: "Endereco", "Documento de identificacao"): legenda do fieldset
    // ou titulo do painel/cartao mais proximo. Ajuda a separar "UF do endereco" de "UF do RG".
    const block = e.closest('fieldset,section,[class*=panel],[class*=card],[class*=secao],[class*=grupo]');
    const section = txt(
      block?.querySelector('legend,h1,h2,h3,h4,h5,h6,.panel-heading,.panel-title,.card-header,.card-title') || null
    ).slice(0, 100);

    const noStar = s => (s || '').replace(/\*/g, ' ').replace(/\s+/g, ' ').trim();
    const rawLabel = [labels, aria].filter(Boolean).join(' ') ||
      [parentLabel, rowLabel].filter(Boolean).join(' ');
    // Rotulo que e so "*" (a estrela fica numa celula propria): usa o texto das outras celulas da linha
    const tr = e.closest('tr');
    const rowOthers = tr ? noStar([...tr.cells].filter(c => !c.contains(e)).map(txt).join(' ')).slice(0, 100) : '';
    const ownLabel = noStar(rawLabel) ? rawLabel : (rowOthers || rawLabel);
    // Obrigatorio: atributo, "*" no rotulo ou aviso "campo obrigatorio" na mesma linha
    // (os TRTs so mostram o aviso depois de tentar enviar).
    const row = e.closest('tr');
    // so a linha do proprio campo (tabela de layout aninhada: a linha mais proxima)
    const rowWarn = row && row.querySelectorAll('input,select,textarea').length <= 3 ? txt(row) : '';
    const star = /(^|\s)\*(\s|:|$)/;  // "*" como marca de obrigatorio, antes ou depois do rotulo
    const marked = star.test(rawLabel) || star.test(ownLabel) || /\S\*\s*:?$/.test(rawLabel) ||
      /campo obrigat|preenchimento obrigat/i.test(rowWarn.replace(ownLabel, ''));

    const options = e.tagName === 'SELECT'
      ? [...e.options].map(o => ({
          value: o.value,
          label: txt(o)
        })).slice(0, 250)
      : [];

    return {
      index,
      tag: e.tagName.toLowerCase(),
      type: (e.type || '').toLowerCase(),
      id: e.id || '',
      name: e.name || '',
      // Rotulo proprio (<label for>, aria) primeiro; o do bloco/linha so quando nao ha rotulo
      // proprio - senao "Logradouro" herdava o "CEP" do primeiro <label> do fieldset.
      label: ownLabel,
      section,
      title: e.title || '',
      placeholder: e.placeholder || '',
      autocomplete: e.autocomplete || '',
      value: e.value || '',
      required: !!e.required ||
        e.getAttribute('aria-required') === 'true' || marked,
      readonly: !!e.readOnly,
      disabled: !!e.disabled,
      checked: !!e.checked,
      options
    };
  }).filter(Boolean);

  const identity = {nome: [], cpf: [], oab: []};

  const subject = s => {
    const t = s.toLowerCase().normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '').trim();

    if (/^nome(?: completo)?\s*:?$/.test(t)) return 'nome';
    if (/^cpf\s*:?$/.test(t)) return 'cpf';
    if (/^(?:inscricao\s+(?:na\s+)?oab|oab)(?:\s*\/\s*[a-z]{2})?\s*:?$/
        .test(t)) return 'oab';
    return null;
  };

  for (const e of document.querySelectorAll(
    'span,label,td,th,dt,strong,b'
  )) {
    if (!visible(e)) continue;

    const key = subject(txt(e));
    if (!key) continue;

    const td = e.closest('td');
    const parent = e.parentElement;

    const siblings = parent
      ? [...parent.children].slice(
          [...parent.children].indexOf(e) + 1
        )
      : [];

    const candidates = [
      e.nextElementSibling,
      ...siblings,
      td?.nextElementSibling,
      td?.nextElementSibling?.querySelector('b,strong,span'),
      parent?.nextElementSibling
    ];

    for (const candidate of candidates) {
      if (!candidate || !visible(candidate)) continue;

      const value = (
        candidate.value || txt(candidate)
      ).trim();

      if (!value || value.length > 100 || subject(value)) {
        continue;
      }

      const numbers = value.replace(/\D/g, '');

      if (key === 'cpf' && numbers.length !== 11) continue;

      if (key === 'nome' &&
          (value.split(/\s+/).length < 2 ||
           /\b(cpf|oab)\b/i.test(value))) continue;

      if (key === 'oab' &&
          (!/\d/.test(value) || !/[A-Za-z]/.test(value))) {
        continue;
      }

      if (!identity[key].includes(value)) {
        identity[key].push(value);
      }
      break;
    }
  }


  const location = [];

  const isLocationLabel = value =>
    /^(?:munic[i\u00ed]pio|cidade)\s*\/\s*uf\s*:?\s*$/i
      .test(value.trim());

  for (const el of document.querySelectorAll(
    'span,label,td,th,dt,strong,b'
  )) {
    if (!visible(el) || !isLocationLabel(txt(el))) continue;

    const cell = el.closest('td,th');
    const parent = el.parentElement;

    const siblings = parent
      ? [...parent.children].slice(
          [...parent.children].indexOf(el) + 1
        )
      : [];

    const candidates = [
      el.nextElementSibling,
      ...siblings,
      cell?.nextElementSibling,
      parent?.nextElementSibling
    ];

    for (const candidate of candidates) {
      if (!candidate || !visible(candidate)) continue;

      const value = txt(candidate).trim();

      if (
        !value ||
        value.length > 100 ||
        isLocationLabel(value)
      ) continue;

      location.push(value);
      break;
    }
  }

  return {
    controls,
    identity,
    location: [...new Set(location)]
  };

}"""


def norm(value: str) -> str:
    text = unicodedata.normalize(
        "NFKD", str(value or "")
    )
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    return re.sub(
        r"\s+", " ", text.casefold()
    ).strip()


def numbers(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


# Identificadores observados no PJe TJAP.
# Têm prioridade sobre correspondências por texto.
KNOWN = {
    "cepfield": "cep",
    "cmplog": "logradouro",
    "cmpnum": "address_number",
    "cmpcmp": "complemento",
    "cmpbair": "bairro",
    "cmpeml": "email",
    "cmpddd": "ddd",
    "cmptel": "phone",
}

# Identificacao semantica para outros formularios PJe.
PATTERNS = {
    "nome": r"\bnome(?: completo)?\b",
    "cpf": r"\bcpf\b",
    "oab": r"\boab\b|\binscricao profissional\b|\binscricao principal\b",
    "email": r"\be.?mail\b|\bcorreio eletronico\b",
    "email_confirm": r"\be.?mail\b.*\bconfirma|\bconfirm\w* (?:o |do |de )?e.?mail\b|\brepit\w* .*e.?mail\b",
    "rg_numero": r"\b(?:rg|identidade|registro geral)\b",
    "rg_orgao": r"\borgao\b.*\b(?:expedi\w*|emissor|emissao)\b|\b(?:orgao|emissor) (?:do )?rg\b",
    "rg_data": r"\bdata\b.*\b(?:expedicao|emissao)\b",
    "estado_civil": r"\bestado civil\b",
    "ddd": r"\bddd\b",
    "phone": r"\btelefone\b|\bcelular\b|\bfone\b",
    "cep": r"\bcep\b|\bcodigo postal\b",
    "logradouro": r"\blogradouro\b|\bendereco\b|\brua\b|\bavenida\b",
    "address_number": r"\bnumero\b|\bnumeracao\b|\bn[.°º]\b",
    "complemento": r"\bcomplemento\b",
    "bairro": r"\bbairro\b",
    "cidade": r"\bcidade\b|\bmunicipio\b|\blocalidade\b",
    "uf": r"\buf\b|\bestado\b(?! civil)|\bunidade federativa\b",
}

UF_NAMES = {
    "AC": "acre",
    "AL": "alagoas",
    "AP": "amapa",
    "AM": "amazonas",
    "BA": "bahia",
    "CE": "ceara",
    "DF": "distrito federal",
    "ES": "espirito santo",
    "GO": "goias",
    "MA": "maranhao",
    "MT": "mato grosso",
    "MS": "mato grosso do sul",
    "MG": "minas gerais",
    "PA": "para",
    "PB": "paraiba",
    "PR": "parana",
    "PE": "pernambuco",
    "PI": "piaui",
    "RJ": "rio de janeiro",
    "RN": "rio grande do norte",
    "RS": "rio grande do sul",
    "RO": "rondonia",
    "RR": "roraima",
    "SC": "santa catarina",
    "SP": "sao paulo",
    "SE": "sergipe",
    "TO": "tocantins",
}

IDENTITY = ("nome", "cpf", "oab")

# Quando dois padroes batem no mesmo rotulo, o mais especifico vence:
# "E-mail (confirmacao)" e confirmacao, nao o e-mail; "Numero do RG" e RG, nao o numero da casa.
DOMINATES = {
    # "Endereco de e-mail" e e-mail, nao logradouro
    "email": {"logradouro"},
    "email_confirm": {"email", "logradouro"},
    "rg_data": {"rg_numero", "rg_orgao"},
    "rg_orgao": {"rg_numero"},
    "rg_numero": {"address_number"},
    # "UF do RG"/"UF do orgao expedidor": e uma UF (de outro assunto, tratada em other_subject)
    "uf": {"rg_numero", "rg_orgao"},
}


def _specific(matches: list[str]) -> list[str]:
    dropped = set().union(*(DOMINATES.get(m, set()) for m in matches)) if matches else set()
    return [m for m in matches if m not in dropped]


def field_key(
    field: dict[str, Any]
) -> tuple[str | None, int]:

    ident = norm(
        field.get("id", "").split(":")[-1]
    )
    name = norm(
        field.get("name", "").split(":")[-1]
    )

    for code, key in KNOWN.items():
        if ident == code or name == code:
            return key, 100

    captions = norm(" ".join(
        str(field.get(key, ""))
        for key in (
            "label",
            "placeholder",
            "title",
            "autocomplete",
        )
    ))

    matches = _specific([
        key for key, pattern in PATTERNS.items()
        if re.search(pattern, captions)
    ])

    if len(matches) == 1:
        return matches[0], 80

    ids = norm(" ".join((ident, name)))

    matches = _specific([
        key for key, pattern in PATTERNS.items()
        if re.search(pattern, ids)
    ])

    if len(matches) == 1:
        return matches[0], 50

    return None, 0


ADDRESS_KEYS = ("cep", "logradouro", "address_number", "complemento", "bairro", "cidade", "uf")

# Divergencia SO de endereco de rua: usa o valor da planilha e segue sem parar.
# cidade/uf ficam de fora de proposito (divergencia maior merece conferencia).
RUA_KEYS = ("cep", "logradouro", "address_number", "complemento", "bairro")

# Campos que parecem endereco/contato mas sao de outro assunto (RG, OAB, trabalho...).
NOT_ADDRESS = re.compile(
    r"\b(rg|identidade|expedi\w*|emissor|orgao emissor|oab|seccional|inscricao|naturalidade|"
    r"nascimento|natural|profissional|trabalho|comercial|empresa|titulo de eleitor|ctps|conselho)\b"
)
NOT_PHONE = re.compile(r"\b(fax|recado|comercial)\b")


def other_subject(key: str, field: dict[str, Any]) -> bool:
    """True quando o controle trata de outro assunto apesar do rotulo parecido."""
    text = norm(" ".join(str(field.get(k, "")) for k in ("label", "section", "title", "placeholder")))
    if key in ADDRESS_KEYS:
        return NOT_ADDRESS.search(text) is not None
    if key in ("phone", "ddd"):
        return NOT_PHONE.search(text) is not None
    return False


def has_value(field: dict[str, Any]) -> bool:
    """Ja preenchido (lista com opcao escolhida que nao e "Selecione...")."""
    value = str(field.get("value") or "").strip()
    if not value or value in ("0", "-1"):
        return False
    if field.get("tag") == "select":
        label = next((o.get("label", "") for o in field.get("options", []) if o.get("value") == value), "")
        return not re.search(r"^(selecione|escolha|informe|--)", norm(label))
    return True


def pinned(field: dict[str, Any], specs: list[str]) -> bool:
    """Campo fixado no .toml do tribunal: "id=...", "name=..." ou "label=trecho do rotulo"."""
    for spec in specs:
        kind, _, value = spec.partition("=")
        value = value.strip()
        if not value:
            continue
        if kind == "id" and field.get("id") == value:
            return True
        if kind == "name" and field.get("name") == value:
            return True
        if kind == "label" and norm(value) in norm(field.get("label", "")):
            return True
    return False


def break_tie(key: str, winners: list[dict[str, Any]], expected: dict[str, str],
              anchor: dict[str, Any] | None) -> dict[str, Any] | list | None:
    """Desempata candidatos ao mesmo campo sem chutar.

    1. so um ja tem o valor certo -> ele; todos ja tem o valor certo -> lista (nada a fazer);
    2. endereco: o que esta no bloco "Endereco" ou no mesmo bloco do CEP;
    3. endereco: o mais proximo do CEP na tela, se for unico.
    """
    wanted = expected.get(key, "")
    if wanted:
        ok = [f for f in winners if f.get("value") and same(key, f["value"], wanted)]
        if len(ok) == len(winners):
            return winners
        if len(ok) == 1:
            return ok[0]
    if key not in ADDRESS_KEYS:
        return None
    in_address = [f for f in winners if re.search(r"\bendereco|residencia|domicilio\b", norm(f.get("section", "")))]
    if len(in_address) == 1:
        return in_address[0]
    if anchor is not None:
        same_block = [f for f in winners if f.get("section") and f.get("section") == anchor.get("section")]
        if len(same_block) == 1:
            return same_block[0]
        distances = sorted((abs(f["index"] - anchor["index"]), i) for i, f in enumerate(winners))
        if len(distances) > 1 and distances[0][0] < distances[1][0]:
            return winners[distances[0][1]]
    return None


def desired(record: Any) -> dict[str, str]:
    address = record.endereco
    phone = numbers(record.telefone)

    return {
        "nome": record.nome_esperado,
        "cpf": numbers(record.cpf_esperado),
        "oab": (
            f"{record.oab_numero}/{record.oab_uf}"
        ),
        "email": record.email,
        "email_confirm": record.email,
        "rg_numero": getattr(record, "rg_numero", ""),
        "rg_orgao": getattr(record, "rg_orgao", ""),
        "rg_data": getattr(record, "rg_data_expedicao", ""),
        "estado_civil": getattr(record, "estado_civil", ""),
        "phone": (
            phone[2:]
            if len(phone) in (10, 11)
            else phone
        ),
        "ddd": (
            phone[:2]
            if len(phone) in (10, 11)
            else ""
        ),
        "cep": numbers(address.cep),
        "logradouro": address.logradouro,
        "address_number": address.numero,
        "complemento": address.complemento,
        "bairro": address.bairro,
        "cidade": address.cidade,
        "uf": address.uf,
    }



def address_equivalent(
    key: str,
    actual: str,
    expected: str,
) -> bool:
    """Compara apenas abreviacoes e formatacao equivalentes."""

    aliases = {
        "logradouro": {
            "r": "rua",
            "av": "avenida",
            "rod": "rodovia",
            "estr": "estrada",
            "est": "estrada",
            "trav": "travessa",
            "tv": "travessa",
        },
        "complemento": {
            "apt": "apartamento",
            "apto": "apartamento",
            "ap": "apartamento",
            "apart": "apartamento",
            "bl": "bloco",
        },
    }

    def canonical(value: str) -> str:
        value = norm(value)
        value = re.sub("[^a-z0-9 ]", " ", value)
        tokens = value.split()
        return " ".join(
            aliases[key].get(token, token)
            for token in tokens
        )

    return canonical(actual) == canonical(expected)


def oab_check(text: str, numero: str, uf: str) -> str:
    """Confere a OAB exibida pelo PJe, tolerando textos extras como "(Advogado)".

    Retorna "ok", "uf_nao_exibida" (numero confere e nenhuma UF aparece ao lado),
    "uf_divergente" ou "numero_divergente".
    """
    value = norm(text)
    wanted = numbers(numero).lstrip("0")
    if not wanted:
        return "numero_divergente"

    hits = [
        m for m in re.finditer(r"\d[\d.\-]*\d|\d", value)
        if numbers(m.group()).lstrip("0") == wanted
    ]
    if not hits:
        return "numero_divergente"

    ufs = {code.lower() for code in UF_NAMES}
    seen = set()
    for m in hits:
        before = re.search(r"(?<![a-z])([a-z]{2})[\s/:\-]{0,3}$", value[:m.start()])
        after = re.match(r"[\s/:\-]{0,3}([a-z]{2})(?![a-z])", value[m.end():])
        for found in (before, after):
            if found and found.group(1) in ufs:
                seen.add(found.group(1))
    for found in re.finditer(r"\boab\s*[/:\-]?\s*([a-z]{2})(?![a-z])", value):
        if found.group(1) in ufs:
            seen.add(found.group(1))

    expected_uf = norm(str(uf))
    if expected_uf in seen:
        return "ok"
    if seen:
        return "uf_divergente"
    return "uf_nao_exibida"


def _nome_subsequencia(a: str, b: str) -> bool:
    """Mesma pessoa tolerando um sobrenome a menos (certificado/PJe as vezes truncam o nome).

    Aceita nomes iguais ou quando o mais curto e subsequencia (na ordem) do mais longo, com
    pelo menos 2 nomes em comum. O CPF e conferido a parte e e a garantia de identidade.
    """
    ta, tb = norm(a).split(), norm(b).split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    i = 0
    for token in longo:
        if i < len(curto) and curto[i] == token:
            i += 1
    return i == len(curto) and len(curto) >= 2


def same(
    key: str,
    actual: str,
    expected: str
) -> bool:

    if key == "nome":
        return _nome_subsequencia(actual, expected)

    if key in (
        "cpf",
        "cep",
        "phone",
        "ddd",
        "address_number",
    ):
        a = numbers(actual)
        b = numbers(expected)
        return a == b if a and b else norm(actual) == norm(expected)

    if key == "oab":
        numero, _, uf = expected.rpartition("/")
        return oab_check(actual, numero, uf) in ("ok", "uf_nao_exibida")

    if key in ("logradouro", "complemento"):
        return address_equivalent(key, actual, expected)

    if key == "rg_data":
        return numbers(actual) == numbers(expected)

    if key == "estado_civil":
        return bool(civil_stem(actual)) and civil_stem(actual) == civil_stem(expected)

    if key in ("rg_numero", "rg_orgao"):
        return re.sub(r"[^0-9a-z]", "", norm(actual)) == re.sub(r"[^0-9a-z]", "", norm(expected))

    return norm(actual) == norm(expected)


def static_location_matches(
    value: str,
    city: str,
    uf: str,
) -> bool:
    """Compara o texto "Municipio/UF" do PJe com a cidade e a UF da planilha.

    Aceita "Belo Horizonte/MG", "BELO HORIZONTE - MG", "Belo Horizonte (MG)",
    "Belo Horizonte, Minas Gerais", com ou sem acento e em qualquer caixa.
    """
    text = norm(value)
    match = re.fullmatch(r"(.+?)\s*(?:[/,\-\u2013]\s*|\(\s*)([a-z ]+?)\s*\)?", text)
    if not match:
        return False
    found_city, found_state = match.group(1).strip(), match.group(2).strip()

    def bare(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", norm(name)).strip()

    expected_state = norm(UF_NAMES.get(str(uf).upper(), ""))
    return bare(found_city) == bare(city) and found_state in {norm(uf), expected_state}


def scan(page: Any) -> dict[str, Any]:
    return page.evaluate(JS_SNAPSHOT)


def civil_stem(value: str) -> str:
    """"Solteiro(a)", "SOLTEIRA", "solteiro" -> "solteir"; "Uniao estavel" -> "uniao estavel"."""
    text = re.sub(r"[^a-z ]+", " ", norm(value)).strip()
    if text.startswith("uniao") or "estavel" in text:
        return "uniao estavel"
    first = text.split(" ")[0] if text else ""
    return re.sub(r"(os|as|o|a)$", "", first)


def option_choice(
    field: dict[str, Any],
    value: str,
    key: str = "",
) -> str | None:

    if key == "estado_civil":
        found = [o["value"] for o in field.get("options", [])
                 if civil_stem(value) and civil_stem(o.get("label", "")) == civil_stem(value)]
        return found[0] if len(found) == 1 else None

    candidates = {norm(value)}

    if norm(value).upper() in UF_NAMES:
        candidates.add(
            UF_NAMES[norm(value).upper()]
        )

    found = [
        option["value"]
        for option in field.get("options", [])
        if (
            norm(option.get("value", "")) in candidates
            or norm(option.get("label", "")) in candidates
        )
    ]

    return found[0] if len(found) == 1 else None


def operate(
    page: Any,
    record: Any,
    overrides: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """
    Analisa e preenche o formulario sem enviar.
    O relatorio contem apenas estados, nunca valores pessoais.
    """

    expected = desired(record)

    report: dict[str, Any] = {
        "verified": [],
        "filled": [],
        "preserved": [],
        "absent": [],
        "unknown_optional": 0,
        "pending": [],
        "blocked": [],
    }

    initial = scan(page)
    controls = initial["controls"]

    location_values = initial.get("location", [])

    location_found = bool(location_values)

    location_matches = (
        location_found
        and all(
            static_location_matches(
                value,
                expected["cidade"],
                expected["uf"],
            )
            for value in location_values
        )
    )

    if location_found and not location_matches:
        report["pending"].append(
            "municipio_uf_divergente"
        )

    grouped: dict[
        str, list[tuple[int, dict[str, Any]]]
    ] = {}

    overrides = overrides or {}

    # Classificar todos os controles visiveis.
    for field in controls:
        key, rank = field_key(field)
        fixed = next((k for k, specs in overrides.items() if pinned(field, specs)), None)
        if fixed:
            key, rank = fixed, 1000  # fixado no .toml do tribunal: vence qualquer heuristica
        elif key and other_subject(key, field):
            # Ex.: "UF" do orgao expedidor do RG: nao e o endereco. Sem dado para ele.
            report.setdefault("other_subject", []).append(
                key + ": " + str(field.get("label") or field.get("name") or "?")[:80])
            if field.get("required") and not str(field.get("value") or "").strip():
                report["pending"].append("campo_obrigatorio_sem_dado:" + key)
            continue

        if key is None:
            report.setdefault("unmapped_controls", []).append({
                "tag": field.get("tag"),
                "type": field.get("type"),
                "id": field.get("id"),
                "name": field.get("name"),
                "label": field.get("label"),
                "required": field.get("required"),
                "options": [
                    option.get("label", "")
                    for option in field.get("options", [])
                ][:30],
            })

        if key:
            grouped.setdefault(
                key, []
            ).append((rank, field))

        elif field.get("required") and not has_value(field):
            # Obrigatorio, vazio e sem dado na planilha (ex.: estado civil): precisa de alguem.
            report["pending"].append(
                "campo_obrigatorio_desconhecido"
            )
            report.setdefault("unknown_required", []).append(
                str(field.get("label") or field.get("name") or "?")[:80])
        elif field.get("required"):
            report.setdefault("preserved_unknown", []).append(
                str(field.get("label") or field.get("name") or "?")[:80])

        elif (
            field.get("type") == "checkbox"
            and not field.get("required")
        ):
            if field.get("checked"):
                report["pending"].append(
                    "checkbox_opcional_ja_marcado"
                )
            else:
                report.setdefault(
                    "ignored_optional", []
                ).append("checkbox_desmarcado")

        elif (
            field.get("type") in ("checkbox", "radio")
            or field.get("tag") == "select"
        ):
            # Lista/opcao OPCIONAL que o robo nao conhece (ex.: estado civil, sexo): fica como o PJe
            # deixou. Se o PJe exigir algo ao avancar, a mensagem dele aparece e o robo para.
            report.setdefault("ignored_optional", []).append(
                "lista opcional: " + str(field.get("label") or field.get("name") or "?")[:80])

        else:
            report["unknown_optional"] += 1

    selected = {}

    # Nao escolher arbitrariamente entre campos ambiguos (desempate so com evidencia).
    order = sorted(grouped, key=lambda k: (k != "cep", k))
    for key in order:
        items = grouped[key]
        highest = max(
            rank for rank, _ in items
        )

        winners = [
            field for rank, field in items
            if rank == highest
        ]

        if len(winners) > 1:
            chosen = break_tie(key, winners, expected, selected.get("cep"))
            if isinstance(chosen, list):
                report["preserved"].append(key)  # todos ja estao com o valor certo
                continue
            if chosen is not None:
                report.setdefault("tie_broken", []).append(key)
                winners = [chosen]

        if len(winners) != 1:
            report["pending"].append(
                "campo_ambiguo:" + key
            )
            # Rotulos dos candidatos (texto do formulario, nunca valores) para o diagnostico.
            # (lista de textos: a auditoria redige chaves como "logradouro"/"cep" em dicionarios)
            report.setdefault("ambiguous", []).append(key + ": " + " | ".join(
                str(f.get("label") or f.get("name") or f.get("id") or "?")[:80] for f in winners))
        else:
            selected[key] = winners[0]

    # Telefone completo quando nao existe campo DDD separado.
    if "ddd" not in selected:
        expected["phone"] = numbers(
            record.telefone
        )

    # Validacao das informacoes de identidade.
    for key in IDENTITY:
        values = list(
            initial["identity"].get(key, [])
        )

        field = selected.get(key)

        if key == "oab" and field and field.get("tag") == "select":
            current = str(field.get("value") or "")

            matches = [
                option
                for option in field.get("options", [])
                if str(option.get("value", "")) == current
            ]

            if not current or len(matches) != 1:
                report["pending"].append(
                    "inscricao_principal_nao_selecionada"
                )
                continue

            label = str(matches[0].get("label", ""))
            result = oab_check(label, record.oab_numero, record.oab_uf)

            if result == "numero_divergente":
                if re.search(r"\d", label):
                    report["blocked"].append("identidade_divergente:oab_numero")
                else:
                    report["pending"].append("numero_oab_nao_identificado")
                continue

            if result == "uf_divergente":
                report["blocked"].append("identidade_divergente:oab_uf")
                continue

            report["verified"].append("oab_numero")
            if result == "ok":
                report["verified"].append("oab")
            else:
                # Numero confere e o PJe nao mostra a UF (ex.: "12345 (Advogado)").
                report.setdefault("notes", []).append("oab_uf_nao_exibida")

            continue

        if field and field.get("value"):
            values.append(field["value"])

        values = list(dict.fromkeys(
            value.strip()
            for value in values
            if value.strip()
        ))

        if not values:
            report["pending"].append(
                "identidade_nao_exibida:" + key
            )

        elif any(
            not same(key, value, expected[key])
            for value in values
        ):
            report["blocked"].append(
                "identidade_divergente:" + key
            )

        else:
            report["verified"].append(key)

    # Nome e CPF precisam estar identificados
    # antes de modificar dados cadastrais.
    if report["blocked"] or any(
        "identidade_nao_exibida:" + key
        in report["pending"]
        for key in ("nome", "cpf")
    ):
        return report

    # CEP primeiro: o PJe pode atualizar endereco via AJAX.
    ordered = [
        "cep",
        "logradouro",
        "address_number",
        "complemento",
        "bairro",
        "cidade",
        "uf",
        "email",
        "email_confirm",
        "ddd",
        "phone",
        "rg_numero",
        "rg_orgao",
        "rg_data",
        "estado_civil",
    ]

    for key in ordered:
        value = expected[key]

        if not value:
            field = selected.get(key)
            if field is not None and not has_value(field) and not field.get("readonly"):
                if field.get("required"):
                    report["pending"].append("campo_obrigatorio_sem_dado:" + key)
                else:
                    # Ex.: RG sem coluna na planilha. Os TRTs so avisam que e obrigatorio ao enviar.
                    report.setdefault("sem_dado", []).append(key)
            continue

        chosen = selected.get(key)

        if not chosen:
            if key in ("cidade", "uf") and location_found:
                if location_matches:
                    report["preserved"].append(key)
                continue

            report["absent"].append(key)
            continue

        # Atualizar referencias depois de cada alteracao no DOM.
        fresh = scan(page)
        current = None

        for field in fresh["controls"]:

            if (
                chosen.get("id")
                and field["id"] == chosen["id"]
            ):
                current = field
                break

            if (
                chosen.get("name")
                and field["name"] == chosen["name"]
            ):
                current = field
                break

            if (
                not chosen.get("id")
                and not chosen.get("name")
                and field_key(field)[0] == key
                and field["index"] == chosen["index"]
            ):
                current = field
                break

        if current is None:
            report["pending"].append(
                "campo_desapareceu:" + key
            )
            continue

        # Campos bloqueados ou de somente leitura.
        if current["disabled"] or current["readonly"]:
            if same(key, current["value"], value):
                report["preserved"].append(key)
            elif key in ADDRESS_KEYS and str(current["value"] or "").strip():
                # Endereco preenchido pelo PJe (ex.: pelo CEP) e bloqueado: vale o do PJe.
                report["preserved"].append(key)
                report.setdefault("preserved_divergent", []).append(key)
            else:
                report["pending"].append(
                    "campo_nao_editavel:" + key
                )
                # So o estado (vazio/preenchido), nunca o valor.
                report.setdefault("non_editable", []).append(key + ": " + (
                    "preenchido pelo PJe" if str(current["value"] or "").strip() else "vazio"))
            continue

        if current["type"] in ("checkbox", "radio"):
            report["pending"].append(
                "controle_sem_automacao:" + key
            )
            continue

        # Valor ja correto: nao alterar.
        shown = current["value"]
        if current["tag"] == "select":  # compara tambem o texto da opcao escolhida
            shown = next((o["label"] for o in current["options"] if o["value"] == current["value"]), shown)
        if (
            current["value"]
            and (same(key, current["value"], value) or same(key, shown, value))
        ):
            report["preserved"].append(key)
            continue

        # Tratar placeholders de listas como campos vazios.
        raw = norm(current["value"])

        placeholder = (
            current["tag"] == "select"
            and (
                not raw
                or raw in ("0", "-1")
                or any(
                    option["value"] == current["value"]
                    and re.search(
                        r"^(selecione|escolha|informe|--)",
                        norm(option["label"])
                    )
                    for option in current["options"]
                )
            )
        )

        # Nao sobrescrever valores divergentes - EXCETO endereco de rua, que passa a
        # usar o valor da planilha e seguir sem parar (decisao do operador).
        if current["value"] and not placeholder and key not in RUA_KEYS:
            report["pending"].append(
                "valor_preexistente_divergente:" + key
            )
            continue

        target = page.locator(
            "input,select,textarea"
        ).nth(current["index"])

        try:
            if current["tag"] == "select":
                option = option_choice(
                    current, value, key
                )

                if option is None:
                    report["pending"].append(
                        "opcao_indisponivel:" + key
                    )
                    continue

                target.select_option(
                    value=option,
                    timeout=4000
                )
            else:
                target.fill(
                    str(value),
                    timeout=4000
                )

            # Confirmar se o valor permaneceu apos a escrita.
            after = scan(page)["controls"]

            changed = next(
                (
                    item for item in after
                    if (
                        current.get("id")
                        and item["id"] == current["id"]
                    )
                ),
                None
            )

            if changed is None:
                changed = next(
                    (
                        item for item in after
                        if (
                            current.get("name")
                            and item["name"] == current["name"]
                        )
                    ),
                    None
                )

            if changed is None:
                changed = next(
                    (
                        item for item in after
                        if (
                            item["index"] == current["index"]
                            and field_key(item)[0] == key
                        )
                    ),
                    None
                )

            changed_text = changed["value"] if changed else ""
            if changed and changed.get("tag") == "select":
                changed_text = next((o["label"] for o in changed.get("options", [])
                                     if o["value"] == changed["value"]), changed_text)
            if (
                changed
                and (same(key, changed["value"], value) or same(key, changed_text, value))
            ):
                report["filled"].append(key)
            else:
                report["pending"].append(
                    "nao_confirmado:" + key
                )

        except Exception:
            report["pending"].append(
                "falha_preenchimento:" + key
            )

    # Relatorio seguro, sem nome, CPF, OAB ou contatos.
    for section in (
        "verified",
        "filled",
        "preserved",
        "absent",
        "pending",
        "blocked",
    ):
        report[section] = sorted(
            set(report[section])
        )

    return report


# ---------------------------------------------------------------- botoes de acao
JS_ACTIONS = r"""() => {
  const visible = e => !!(e.getClientRects().length) &&
    getComputedStyle(e).visibility !== 'hidden';
  const txt = e => (e.innerText || e.value || e.getAttribute('aria-label') ||
    e.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
  const nodes = [...document.querySelectorAll(
    'button,input[type=submit],input[type=button],a,[role=button]'
  )];
  return nodes.map((e, index) => {
    if (!visible(e) || e.disabled || e.getAttribute('aria-disabled') === 'true') return null;
    const text = txt(e);
    if (!text || text.length > 60) return null;
    const form = e.closest('form');
    return {
      index,
      text,
      submit: (e.type || '').toLowerCase() === 'submit',
      in_form: !!(form && form.querySelector('input:not([type=hidden]),select,textarea')),
    };
  }).filter(Boolean);
}"""

JS_MARK_ACTION = r"""([index, mark]) => {
  document.querySelectorAll('[data-pje-rpa-action]')
    .forEach(e => e.removeAttribute('data-pje-rpa-action'));
  const nodes = [...document.querySelectorAll(
    'button,input[type=submit],input[type=button],a,[role=button]'
  )];
  const el = nodes[index];
  if (!el) return false;
  el.setAttribute('data-pje-rpa-action', mark);
  return true;
}"""

JS_MESSAGES = r"""() => {
  const visible = e => !!(e.getClientRects().length) &&
    getComputedStyle(e).visibility !== 'hidden';
  const sel = '.rich-messages,.rich-message,.ui-messages-error,.ui-message-error,' +
    '.alert-danger,.alert-error,.erro,.error,.errors,.mensagem-erro,[role=alert],' +
    '.mat-error,.invalid-feedback,#input-error,.pf-m-error,.kc-feedback-text,.alert-warning';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (t && t.length <= 200) out.push(t);
  }
  return [...new Set(out)].slice(0, 10);
}"""

ACTION_RULES = {
    # padrao forte (texto completo conhecido), padrao aceito
    "advance": (
        r"confirmar dados e prosseguir|salvar e prosseguir|salvar e continuar",
        r"\b(prosseguir|continuar|confirmar|avancar|proximo|proxima|seguinte|"
        r"salvar|gravar|concluir|finalizar|enviar|cadastrar)\b",
    ),
    "sign": (
        r"assinar termo( de compromisso)?|assinar e (enviar|concluir|prosseguir)",
        r"\bassinar\b",
    ),
}

ACTION_NEGATIVE = (
    r"\b(cancelar|voltar|sair|limpar|excluir|remover|desistir|fechar|imprimir|"
    r"pesquisar|buscar|consultar|anterior|logout|ajuda|incluir|adicionar|novo|nova|"
    r"alterar|editar|baixar|download|visualizar|nao)\b"
)


def rank_actions(
    candidates: list[dict[str, Any]],
    purpose: str,
) -> list[tuple[int, dict[str, Any]]]:
    strong, accepted = ACTION_RULES[purpose]
    ranked = []
    for item in candidates:
        text = norm(item.get("text", ""))
        if not text or re.search(ACTION_NEGATIVE, text):
            continue
        if re.search(strong, text):
            score = 100
        elif re.search(accepted, text):
            score = 50
        else:
            continue
        if item.get("submit"):
            score += 10
        if item.get("in_form"):
            score += 5
        ranked.append((score, item))
    ranked.sort(key=lambda pair: -pair[0])
    return ranked


def choose_action(
    candidates: list[dict[str, Any]],
    purpose: str,
) -> dict[str, Any] | None:
    """Escolhe um unico botao. Empate entre textos diferentes = ambiguo (None)."""
    ranked = rank_actions(candidates, purpose)
    if not ranked:
        return None
    best_score, best = ranked[0]
    rivals = {
        norm(item["text"])
        for score, item in ranked
        if score == best_score
    }
    if len(rivals) > 1:
        return {"ambiguous": sorted(rivals)}
    return best


def locate_action(page: Any, purpose: str) -> dict[str, Any] | None:
    """Retorna {"text": ...} do botao escolhido, {"ambiguous": [...]} ou None."""
    chosen = choose_action(page.evaluate(JS_ACTIONS), purpose)
    if not chosen or "ambiguous" in chosen:
        return chosen
    if not page.evaluate(JS_MARK_ACTION, [chosen["index"], purpose]):
        return None
    return {"text": chosen["text"], "source": "adaptive"}


def click_action(page: Any, purpose: str) -> None:
    page.locator(f'[data-pje-rpa-action="{purpose}"]').first.click()


def page_messages(page: Any) -> list[str]:
    try:
        return list(page.evaluate(JS_MESSAGES))
    except Exception:
        return []
