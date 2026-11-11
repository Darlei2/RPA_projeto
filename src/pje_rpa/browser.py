from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from urllib.parse import urlparse

from .models import AppConfig, LawyerRecord

_OUTLINE_JS = """() => {
  const seen = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const text = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const pick = sel => [...document.querySelectorAll(sel)].filter(seen).map(text).filter(Boolean).slice(0, 40);
  return {
    title: document.title,
    headings: pick('h1,h2,h3,h4,legend,.titulo,.title'),
    labels: pick('label'),
    identity_nodes: [...document.querySelectorAll('td,th,span,div,strong,b')].filter(seen).filter(el => { const v=(el.innerText||'').trim(); return v.length>0 && v.length<120 && /nome|cpf|oab/i.test(v) && ![...el.children].some(c=>/nome|cpf|oab/i.test(c.innerText||'')); }).slice(0,30).map(el=>({topic:((el.innerText||'').match(/nome|cpf|oab/i)||[''])[0].toLowerCase(),tag:el.tagName.toLowerCase(),id:el.id||'',parent_tag:el.parentElement?.tagName.toLowerCase()||'',parent_id:el.parentElement?.id||'',siblings:[...(el.parentElement?.children||[])].slice(0,6).map(x=>({tag:x.tagName.toLowerCase(),id:x.id||''}))})),
    buttons: pick('button,input[type=submit],input[type=button],a[role=button],[role=button]'),
    inputs: [...document.querySelectorAll('input,select,textarea')].filter(seen).slice(0, 40)
      .map(el => ({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || ''})),
  };
}"""


PDPJ_SSO_ORIGIN = "https://sso.cloud.pje.jus.br"


def _local_network_origins(base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    return list(dict.fromkeys([f"{parsed.scheme}://{parsed.netloc}", PDPJ_SSO_ORIGIN]))


class BrowserError(RuntimeError):
    pass


class BrowserPort(ABC):
    @abstractmethod
    def open(self, url: str) -> None: ...

    @abstractmethod
    def visible(self, group: str, timeout_ms: int | None = None) -> bool: ...

    def wait_hidden(self, group: str, timeout_ms: int) -> bool:
        """Espera nenhum seletor do grupo estar visivel (ex.: a tela de login sumir)."""
        deadline = time.monotonic() + timeout_ms / 1000
        while self.visible(group, 1):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)
        return True

    @abstractmethod
    def click(self, group: str) -> None: ...

    @abstractmethod
    def fill(self, group: str, value: str) -> None: ...

    @abstractmethod
    def read(self, group: str) -> str: ...

    @abstractmethod
    def page_text_contains(self, markers: list[str]) -> str | None: ...

    @abstractmethod
    def close(self) -> None: ...

    def reset_session(self) -> None:
        """Sai do PJe: descarta cookies/sessao do navegador (equivale a deslogar)."""

    def outline(self) -> dict[str, Any]:
        """Estrutura visivel da tela atual, sem valores de campos (para homologacao)."""
        return {}


class PlaywrightBrowser(BrowserPort):
    """Adaptador web. Nao tenta controlar janelas nativas do certificado."""

    def __init__(self, config: AppConfig) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError(
                "Playwright nao instalado. Execute scripts\\preparar.ps1 antes do modo live."
            ) from exc

        self.config = config
        self._pw = sync_playwright().start()
        launcher = getattr(self._pw, config.browser.engine, None)
        if launcher is None:
            raise BrowserError(f"Engine Playwright desconhecido: {config.browser.engine}")
        # Contexto descartavel por execucao: sem cookies/sessao de um advogado anterior,
        # que poderiam levar ao painel de outra pessoa e a um falso ALREADY_REGISTERED.
        self._browser = launcher.launch(
            channel=config.browser.channel or None,
            headless=config.browser.headless,
        )
        self._context = self._browser.new_context(accept_downloads=False)
        # O login por certificado faz a pagina chamar o PJeOffice em localhost. Edge/Chrome recentes
        # pedem "Acessar outros aplicativos e servicos neste dispositivo" e, sem a permissao, o
        # PJeOffice nunca e acionado. Concede somente ao tribunal e ao login PDPJ.
        for origin in _local_network_origins(config.base_url):
            try:
                self._context.grant_permissions(["local-network-access"], origin=origin)
            except Exception:
                pass  # versoes antigas nao tem essa permissao (e tambem nao fazem a pergunta)
        self.page = self._context.new_page()
        self.last_spec = ""  # ultimo seletor que casou (para a sondagem de tribunais)
        self.page.set_default_timeout(config.browser.timeout_ms)

    def open(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")

    def reset_session(self) -> None:
        self._context.clear_cookies()
        try:
            self.page.goto("about:blank")
        except Exception:
            pass

    def visible(self, group: str, timeout_ms: int | None = None) -> bool:
        return self._wait_visible(group, timeout_ms or 1200) is not None

    def click(self, group: str) -> None:
        locator = self._first_visible(group)
        locator.click()

    def fill(self, group: str, value: str) -> None:
        locator = self._first_visible(group)
        try:
            tag = locator.evaluate("e => e.tagName.toLowerCase()")
            if tag not in ("input", "textarea", "select") and not locator.evaluate("e => e.isContentEditable"):
                # O rotulo achou o painel (ex.: <div role=region aria-labelledby="Nova senha">):
                # usa o campo dentro dele.
                inner = locator.locator("input:not([type=hidden]):visible,textarea:visible").first
                if inner.count():
                    locator = inner
            locator.fill(value)
        except Exception as exc:  # noqa: BLE001
            # Nunca repassar a mensagem do Playwright: ela traz o valor digitado (ex.: a senha).
            raise BrowserError(f"Nao foi possivel preencher o campo '{group}' ({type(exc).__name__})") from None

    def read(self, group: str) -> str:
        try:
            locator = self._first_visible(group)
        except BrowserError:
            if group not in ('detected_name', 'detected_cpf', 'detected_oab'): raise
            value = self.page.evaluate('(g)=>{const p={detected_name:/^nome(?: completo)?\\s*:?\\s*$/i,detected_cpf:/^cpf\\s*:?\\s*$/i,detected_oab:/^(?:n\\W*)?oab\\b.*$/i}[g];if(!p)return null;for(const el of document.querySelectorAll(`span,label,th,td,dt`)){if(!el.getClientRects().length)continue;const t=(el.innerText||``).trim();if(t.length>35||!p.test(t))continue;const td=el.closest(`td`),par=el.parentElement;const cand=[el.nextElementSibling,...(par?[...par.children].slice([...par.children].indexOf(el)+1):[]),td?.nextElementSibling,td?.nextElementSibling?.querySelector(`b,strong,span`),par?.nextElementSibling];for(const c of cand){const v=(c?.value||c?.innerText||``).trim();if(!v||v.length>120)continue;if(g===`detected_cpf`){if(v.replace(/\\D/g,``).length===11)return v;}else if(g===`detected_oab`){if(/\\d/.test(v)&&/[A-Za-z]{2}/.test(v))return v;}else if(v.split(/\\s+/).length>=2&&/[A-Za-z]/.test(v)&&!/\\b(?:cpf|oab)\\b/i.test(v))return v;}}return null}', group)
            if value: return str(value).strip()
            raise BrowserError(group)
        try:
            value = locator.input_value()
            if value:
                return value.strip()
        except Exception:
            pass
        return locator.inner_text().strip()

    def page_text_contains(self, markers: list[str]) -> str | None:
        try:
            body = self.page.locator("body").inner_text(timeout=1500).casefold()
        except Exception:
            return None
        return next((marker for marker in markers if marker.casefold() in body), None)

    def close(self) -> None:
        errors = []
        for name, action in (
            ("context", self._context.close),
            ("browser", self._browser.close),
            ("playwright", self._pw.stop),
        ):
            try:
                action()
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}")
        if errors:
            raise BrowserError(
                "Falha ao encerrar Edge/Playwright: "
                + ", ".join(errors)
            )

    def outline(self) -> dict[str, Any]:
        try:
            parsed = urlparse(self.page.url)
            items = self.page.evaluate(_OUTLINE_JS)
        except Exception as exc:
            return {"error": type(exc).__name__}
        # Somente host e caminho: a query pode carregar tokens de sessao.
        return {"url": f"{parsed.netloc}{parsed.path}", **items}

    def _specs(self, group: str) -> list[str]:
        try:
            return self.config.selectors[group]
        except KeyError as exc:
            raise BrowserError(f"Grupo de seletor nao configurado: {group}") from exc

    def _first_visible(self, group: str) -> Any:
        locator = self._wait_visible(group, 5000)
        if locator is None:
            raise BrowserError(f"Nenhum seletor visivel para o grupo: {group}")
        return locator

    def _wait_visible(self, group: str, timeout_ms: int) -> Any | None:
        # locator.is_visible() nao espera (o timeout e ignorado pelo Playwright); por isso
        # alterna entre os seletores candidatos ate o prazo, ja que paginas do PJe renderizam via JS.
        specs = self._specs(group)
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for spec in specs:
                try:
                    if spec.startswith("url="):
                        # Ex.: url=cadastroConcluido -> a pagina atual contem esse trecho.
                        if spec[4:].casefold() in (self.page.url or "").casefold():
                            self.last_spec = spec
                            return self.page.locator("body")
                        continue
                    locator = self._locator(spec).first
                    if locator.is_visible():
                        self.last_spec = spec
                        return locator
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            self.page.wait_for_timeout(250)

    def _locator(self, spec: str) -> Any:
        if spec.startswith("css="):
            return self.page.locator(spec[4:])
        if spec.startswith("label="):
            return self.page.get_by_label(spec[6:], exact=False)
        if spec.startswith("text="):
            return self.page.get_by_text(spec[5:], exact=False)
        if spec.startswith("role="):
            match = re.fullmatch(r"role=([^|]+)\|name=(.+)", spec)
            if not match:
                raise BrowserError(f"Seletor role invalido: {spec}")
            return self.page.get_by_role(match.group(1), name=match.group(2), exact=False)
        raise BrowserError(f"Prefixo de seletor invalido: {spec}")


class SimulatedBrowser(BrowserPort):
    def __init__(self, record: LawyerRecord, scenario: str = "success") -> None:
        self.record = record
        self.scenario = scenario
        self.stage = "closed"
        self.values: dict[str, str] = {}

    def open(self, url: str) -> None:
        self.stage = "login"

    def visible(self, group: str, timeout_ms: int | None = None) -> bool:
        mapping = {
            "login_certificate_button": "login",
            "registration_form": "registration",
            "detected_name": "registration",
            "detected_cpf": "registration",
            "detected_oab": "registration",
            "email": "registration",
            "phone": "registration",
            "cep": "registration",
            "logradouro": "registration",
            "address_number": "registration",
            "complemento": "registration",
            "bairro": "registration",
            "cidade": "registration",
            "uf": "registration",
            "confirm_data": "registration",
            "commitment_term": "term",
            "sign_term": "term",
            "completion": "completed",
            "lawyer_panel": "panel",
            "second_factor": "second_factor",
            "second_factor_input": "second_factor",
            "second_factor_submit": "second_factor",
        }
        if self.scenario == "already_registered" and group == "lawyer_panel" and self.stage == "post_auth":
            return True
        return mapping.get(group) == self.stage

    def click(self, group: str) -> None:
        transitions = {
            ("login", "login_certificate_button"): "post_auth",
            ("registration", "confirm_data"): "term",
            ("term", "sign_term"): "completed",
            ("second_factor", "second_factor_submit"): "second_factor",
        }
        key = (self.stage, group)
        if key not in transitions:
            raise BrowserError(f"Clique invalido na simulacao: {key}")
        self.stage = transitions[key]

    def human_completed_certificate(self) -> None:
        if self.stage == "post_auth":
            if self.scenario == "second_factor":
                self.stage = "second_factor"
            elif self.scenario in {"already_registered", "captcha", "jus_postulandi"}:
                return
            else:
                self.stage = "registration"

    def human_completed_second_factor(self) -> None:
        if self.stage == "second_factor":
            self.stage = "registration"

    def fill(self, group: str, value: str) -> None:
        if self.stage == "second_factor" and group == "second_factor_input":
            self.values[group] = value
            return
        if self.stage != "registration":
            raise BrowserError("Formulario nao esta disponivel")
        self.values[group] = value

    def read(self, group: str) -> str:
        values = {
            "detected_name": self.record.nome_esperado,
            "detected_cpf": self.record.cpf_esperado,
            "detected_oab": f"{self.record.oab_numero}/{self.record.oab_uf}",
        }
        return values.get(group, self.values.get(group, ""))

    def page_text_contains(self, markers: list[str]) -> str | None:
        if self.scenario == "captcha" and self.stage == "post_auth":
            return next((m for m in markers if "captcha" in m.casefold()), None)
        if self.scenario == "jus_postulandi" and self.stage == "post_auth":
            return next((m for m in markers if "jus postulandi" in m.casefold()), None)
        return None

    def close(self) -> None:
        self.stage = "closed"
