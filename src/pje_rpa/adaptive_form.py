"""Motor adaptativo para formularios PJe.

Inspeciona controles, valida identidade e preenche os campos reconhecidos.
Nao envia formularios, nao confirma termos e nao assina documentos.
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
      label: [labels, aria, parentLabel, rowLabel]
        .filter(Boolean).join(' '),
      title: e.title || '',
      placeholder: e.placeholder || '',
      autocomplete: e.autocomplete || '',
      value: e.value || '',
      required: !!e.required ||
        e.getAttribute('aria-required') === 'true',
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
    /^(?:munic[i?]pio|cidade)\s*\/\s*uf\s*:?\s*$/i
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
    "ddd": r"\bddd\b",
    "phone": r"\btelefone\b|\bcelular\b|\bfone\b",
    "cep": r"\bcep\b|\bcodigo postal\b",
    "logradouro": r"\blogradouro\b|\bendereco\b|\brua\b|\bavenida\b",
    "address_number": r"\bnumero\b|\bnumeracao\b|\bn[.°º]\b",
    "complemento": r"\bcomplemento\b",
    "bairro": r"\bbairro\b",
    "cidade": r"\bcidade\b|\bmunicipio\b|\blocalidade\b",
    "uf": r"\buf\b|\bestado\b|\bunidade federativa\b",
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

    matches = [
        key for key, pattern in PATTERNS.items()
        if re.search(pattern, captions)
    ]

    if len(matches) == 1:
        return matches[0], 80

    ids = norm(" ".join((ident, name)))

    matches = [
        key for key, pattern in PATTERNS.items()
        if re.search(pattern, ids)
    ]

    if len(matches) == 1:
        return matches[0], 50

    return None, 0


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


def same(
    key: str,
    actual: str,
    expected: str
) -> bool:

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
        a = norm(actual)
        b = norm(expected)
        uf = norm(expected.split("/")[-1])

        return (
            numbers(a) == numbers(b)
            and re.search(
                r"\b" + re.escape(uf) + r"\b",
                a
            ) is not None
        )

    if key in ("logradouro", "complemento"):
        return address_equivalent(key, actual, expected)

    return norm(actual) == norm(expected)


def static_location_matches(
    value: str,
    city: str,
    uf: str,
) -> bool:
    parts = re.split(
        r"\s*/\s*",
        norm(value),
        maxsplit=1,
    )

    if len(parts) != 2:
        return False

    expected_state = norm(
        UF_NAMES.get(str(uf).upper(), "")
    )

    return (
        parts[0] == norm(city)
        and parts[1] in {
            norm(uf),
            expected_state,
        }
    )


def scan(page: Any) -> dict[str, Any]:
    return page.evaluate(JS_SNAPSHOT)


def option_choice(
    field: dict[str, Any],
    value: str
) -> str | None:

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
    record: Any
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

    # Classificar todos os controles visiveis.
    for field in controls:
        key, rank = field_key(field)

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

        elif field.get("required"):
            report["pending"].append(
                "campo_obrigatorio_desconhecido"
            )

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
            report["pending"].append(
                "controle_desconhecido_para_revisao"
            )

        else:
            report["unknown_optional"] += 1

    selected = {}

    # Nao escolher arbitrariamente entre campos ambiguos.
    for key, items in grouped.items():
        highest = max(
            rank for rank, _ in items
        )

        winners = [
            field for rank, field in items
            if rank == highest
        ]

        if len(winners) != 1:
            report["pending"].append(
                "campo_ambiguo:" + key
            )
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

            found = re.search(
                r"(?<!\d)\d[\d./-]*\d(?!\d)",
                label,
            )

            if found is None:
                report["pending"].append(
                    "numero_oab_nao_identificado"
                )
                continue

            if numbers(found.group()) != numbers(record.oab_numero):
                report["blocked"].append(
                    "identidade_divergente:oab_numero"
                )
                continue

            report["verified"].append("oab_numero")

            uf = norm(str(record.oab_uf))

            if re.search(
                r"\b" + re.escape(uf) + r"\b",
                norm(label),
            ):
                report["verified"].append("oab")
            else:
                report.setdefault(
                    "unverified", []
                ).append("oab_uf")

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
        "ddd",
        "phone",
    ]

    for key in ordered:
        value = expected[key]

        if not value:
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
            else:
                report["pending"].append(
                    "campo_nao_editavel:" + key
                )
            continue

        if current["type"] in ("checkbox", "radio"):
            report["pending"].append(
                "controle_sem_automacao:" + key
            )
            continue

        # Valor ja correto: nao alterar.
        if (
            current["value"]
            and same(key, current["value"], value)
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

        # Nao sobrescrever valores divergentes.
        if current["value"] and not placeholder:
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
                    current, value
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

            if (
                changed
                and same(key, changed["value"], value)
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
