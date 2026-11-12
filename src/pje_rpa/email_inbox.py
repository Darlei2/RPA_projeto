"""Leitura da caixa de e-mail para pegar o link de criacao de senha enviado pelo PJe.

Usa IMAP (Gmail com senha de app, Microsoft 365, ou qualquer servidor IMAP). As credenciais
da caixa vem do operador na hora (ou de variaveis de ambiente) e nunca vao para disco, log ou
auditoria. As mensagens sao lidas com BODY.PEEK (nao ficam marcadas como lidas) e so o link
e devolvido: o corpo do e-mail nao e guardado em lugar nenhum.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import html
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Callable, Protocol
from urllib.parse import urlparse

IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.com.br": "imap.mail.yahoo.com",
}

_URL = re.compile(r"https://[^\s\"'<>()]+", re.IGNORECASE)
_SUBJECT_HINT = re.compile(r"senha|password|credencia|acesso|redefin|atualiz", re.IGNORECASE)
# Links de acao do Keycloak do PJe (ex.: sso.cloud.pje.jus.br/.../login-actions/action-token?key=...)
_ACTION_HINT = re.compile(r"action-token|login-actions|reset|senha|password|credential", re.IGNORECASE)


def guess_imap_host(address: str) -> str | None:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    return IMAP_HOSTS.get(domain)


def is_pje_link(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "jus.br" or host.endswith(".jus.br"))


def _bodies(msg: Message) -> list[str]:
    parts = msg.walk() if msg.is_multipart() else [msg]
    texts = []
    for part in parts:
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        texts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return texts


def extract_password_link(msg: Message) -> str | None:
    """Primeiro link https de host .jus.br do e-mail, preferindo links de acao de senha."""
    candidates: list[str] = []
    for text in _bodies(msg):
        for raw in _URL.findall(text):
            url = html.unescape(raw).rstrip(".,;")
            if is_pje_link(url) and url not in candidates:
                candidates.append(url)
    for url in candidates:
        if _ACTION_HINT.search(url):
            return url
    return None


def _decoded_subject(msg: Message) -> str:
    parts = email.header.decode_header(msg.get("Subject", ""))
    out = []
    for value, charset in parts:
        out.append(value.decode(charset or "utf-8", errors="replace") if isinstance(value, bytes) else value)
    return "".join(out)


def _received_at(msg: Message) -> datetime | None:
    try:
        parsed = email.utils.parsedate_to_datetime(msg.get("Date", ""))
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def find_link_in_messages(messages: list[Message], since: datetime,
                          exclude: frozenset[str] = frozenset()) -> str | None:
    """Do mais novo para o mais antigo: e-mail depois do pedido, sobre senha, com link .jus.br.

    `exclude`: links que ja estavam na caixa antes do pedido (nunca sao devolvidos)."""
    margin = since - timedelta(minutes=1)  # relogios do servidor e da VM podem diferir um pouco
    dated = [(m, _received_at(m)) for m in messages]
    dated.sort(key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for msg, received in dated:
        if received is None or received < margin:
            continue
        sender = (email.utils.parseaddr(msg.get("From", ""))[1] or "").lower()
        about_password = _SUBJECT_HINT.search(_decoded_subject(msg)) is not None
        if not (about_password or sender.endswith(".jus.br")):
            continue
        link = extract_password_link(msg)
        if link and link not in exclude:
            return link
    return None


def all_password_links(messages: list[Message]) -> set[str]:
    return {link for link in (extract_password_link(m) for m in messages) if link}


class InboxPort(Protocol):
    def known_links(self) -> frozenset[str]: ...

    def wait_for_password_link(self, since: datetime, timeout_s: int = 300, on_wait=None,
                               exclude: frozenset[str] = frozenset()) -> str | None: ...


def _conectar_imap(host: str) -> imaplib.IMAP4:
    # Sem timeout, uma caixa que nao responde deixa o robo parado para sempre (tela em branco).
    return imaplib.IMAP4_SSL(host, timeout=30)


@dataclass
class ImapInbox:
    """Caixa IMAP. Para Gmail: IMAP ativo e uma senha de app (conta com verificacao em 2 etapas)."""

    host: str
    user: str
    password: str
    poll_s: int = 10
    connect: Callable[[str], imaplib.IMAP4] = _conectar_imap

    def check_login(self) -> None:
        conn = self.connect(self.host)
        try:
            conn.login(self.user, self.password)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def known_links(self) -> frozenset[str]:
        """Links de senha que ja estao na caixa (ultimos 2 dias): o link novo tem de ser diferente."""
        return frozenset(all_password_links(self._fetch(datetime.now(timezone.utc) - timedelta(days=1))))

    def wait_for_password_link(self, since: datetime, timeout_s: int = 300,
                               on_wait: Callable[[int], None] | None = None,
                               exclude: frozenset[str] = frozenset()) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            link = find_link_in_messages(self._fetch(since), since, exclude)
            if link:
                return link
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                return None
            if on_wait:
                on_wait(remaining)
            time.sleep(min(self.poll_s, max(remaining, 1)))

    def _folders(self, conn) -> list[str]:
        """Caixa de entrada + pasta de spam (o e-mail do PJe as vezes cai la)."""
        folders = ["INBOX"]
        try:
            _, listing = conn.list()
        except Exception:  # noqa: BLE001
            return folders
        for raw in listing or []:
            line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            if "\\Junk" in line or "\\Spam" in line:
                match = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if match:
                    folders.append(match.group(1))
        return folders

    def _fetch(self, since: datetime) -> list[Message]:
        conn = self.connect(self.host)
        try:
            conn.login(self.user, self.password)
            day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
            messages: list[Message] = []
            for folder in self._folders(conn):
                status, _ = conn.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                _, data = conn.search(None, "SINCE", day)
                ids = (data[0] or b"").split()[-30:]  # so as mais recentes
                for msg_id in ids:
                    _, fetched = conn.fetch(msg_id, "(BODY.PEEK[])")
                    for item in fetched or []:
                        if isinstance(item, tuple) and len(item) > 1:
                            messages.append(email.message_from_bytes(item[1]))
            return messages
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
