"""Ferramentas para expandir o robo a varios tribunais do PJe.

- `sondar`: abre a tela de login do tribunal (sem certificado) e mostra quais seletores
  casaram. Serve para conferir a URL e o tipo de login antes do primeiro ensaio.
- `relatorio`: le runtime/audit e mostra, por tribunal, o melhor resultado obtido,
  conferindo a meta de validacao (N tribunais, com pelo menos 1 TRF, 1 TRT e 1 TJ).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import AppConfig

# Ordem de "melhor resultado" de um tribunal.
STATUS_RANK = {
    "COMPLETED": 5,
    "ALREADY_REGISTERED": 4,
    "REHEARSAL_OK": 3,
    "NEEDS_HUMAN": 2,
    "BLOCKED": 1,
    "FAILED": 0,
}

GOAL_TRIBUNAIS = 5
GOAL_RAMOS = ("TRF", "TRT", "TJ")


def ramo(tribunal: str) -> str:
    """'TRT2 - 1o grau' -> 'TRT'; 'TJAP - 1o grau' -> 'TJ'; 'TRF6' -> 'TRF'."""
    name = tribunal.strip().upper()
    for prefix in ("TRF", "TRT", "TJ"):
        if name.startswith(prefix):
            return prefix
    return "OUTRO"


def sondar(config: AppConfig) -> dict[str, Any]:
    from .browser import PlaywrightBrowser

    browser = PlaywrightBrowser(config)
    result: dict[str, Any] = {"tribunal": config.tribunal, "base_url": config.base_url}
    try:
        browser.open(config.base_url)
        result["url_aberta"] = browser.page.url
        if "login_sso_button" in config.selectors and browser.visible("login_sso_button", 5000):
            result["botao_sso"] = browser.last_spec
            browser.click("login_sso_button")
        else:
            result["botao_sso"] = None
        if browser.visible("login_certificate_button", 15000):
            result["botao_certificado"] = browser.last_spec
            result["ok"] = True
        else:
            result["botao_certificado"] = None
            result["ok"] = False
            result["tela"] = browser.outline()
        result["url_login"] = browser.page.url
        marker = browser.page_text_contains(config.stop_markers.get("captcha", []))
        if marker:
            result["captcha"] = marker
    except Exception as exc:  # noqa: BLE001 - a sondagem so informa
        result["ok"] = False
        result["erro"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
    return result


def relatorio(audit_root: Path, goal: int = GOAL_TRIBUNAIS) -> dict[str, Any]:
    best: dict[str, dict[str, Any]] = {}
    for result_path in sorted(audit_root.glob("*/result.json")):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tribunal = str((data.get("details") or {}).get("tribunal") or "").strip()
        status = str(data.get("status") or "")
        if not tribunal or status not in STATUS_RANK:
            continue
        when = _first_timestamp(result_path.parent / "audit.jsonl")
        key = re.sub(r"\s+", " ", tribunal.upper())
        current = best.get(key)
        candidate = {
            "tribunal": tribunal,
            "ramo": ramo(tribunal),
            "status": status,
            "run_id": data.get("run_id", result_path.parent.name),
            "quando_utc": when,
            "execucoes": (current or {}).get("execucoes", 0) + 1,
        }
        if current is None or STATUS_RANK[status] >= STATUS_RANK[current["status"]]:
            best[key] = candidate
        else:
            current["execucoes"] = candidate["execucoes"]

    rows = sorted(best.values(), key=lambda r: (r["ramo"], r["tribunal"]))
    real = [r for r in rows if r["status"] == "COMPLETED"]
    ramos_ok = {r["ramo"] for r in real}
    faltam = [r for r in GOAL_RAMOS if r not in ramos_ok]
    return {
        "tribunais": rows,
        "meta": {
            "tribunais_com_cadastro_real": len(real),
            "minimo": goal,
            "ramos_faltando": faltam,
            "atingida": len(real) >= goal and not faltam,
        },
    }


def _first_timestamp(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as stream:
            first = stream.readline()
        return json.loads(first).get("timestamp_utc")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def format_relatorio(data: dict[str, Any]) -> str:
    lines = [f"{'RAMO':<6}{'TRIBUNAL':<28}{'MELHOR RESULTADO':<20}{'EXEC.':<7}QUANDO (UTC)"]
    for row in data["tribunais"]:
        lines.append(
            f"{row['ramo']:<6}{row['tribunal'][:27]:<28}{row['status']:<20}"
            f"{row['execucoes']:<7}{(row['quando_utc'] or '')[:16]}"
        )
    meta = data["meta"]
    lines.append("")
    lines.append(
        f"Cadastros reais concluidos: {meta['tribunais_com_cadastro_real']} de {meta['minimo']} tribunais"
    )
    if meta["ramos_faltando"]:
        lines.append("Ainda falta cadastro real em: " + ", ".join(meta["ramos_faltando"]))
    lines.append("META ATINGIDA" if meta["atingida"] else "Meta ainda nao atingida")
    return "\n".join(lines)
