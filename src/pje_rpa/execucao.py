"""Cadastro de um advogado em um tribunal, fora da pagina local (usado pela execucao em lote).

Faz o mesmo que a pagina local (webapp.py): le o A1, instala temporariamente no repositorio
do Windows, roda o fluxo do PJe e remove o certificado ao final, com a mesma lista de
"certificados pendentes" para limpeza se o processo cair no meio.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .audit import AuditLog
from .config import assert_live_ready, assert_rehearsal_ready
from .models import AppConfig, LawyerRecord

Notify = Callable[[str], None]


@dataclass
class ResultadoCadastro:
    status: str          # COMPLETED, ALREADY_REGISTERED, REHEARSAL_OK, NEEDS_HUMAN, BLOCKED, FAILED, ERROR
    reason: str
    run_id: str = ""
    senha: str = field(default="", repr=False)  # criada pelo login unico nesta sessao (nunca vai para log)
    senha_detalhe: str = ""


def pending_path(audit_root: Path) -> Path:
    return audit_root.parent / "certificados-pendentes.txt"


def _pending(audit_root: Path) -> list[str]:
    try:
        return [t for t in pending_path(audit_root).read_text(encoding="ascii").split() if len(t) == 40]
    except FileNotFoundError:
        return []


def _set_pending(audit_root: Path, thumbprints: list[str]) -> None:
    path = pending_path(audit_root)
    if thumbprints:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(set(thumbprints))) + "\n", encoding="ascii")
    else:
        path.unlink(missing_ok=True)


def limpar_pendentes(audit_root: Path, store, notify: Notify = print) -> None:
    """Remove certificados que uma execucao anterior instalou e nao conseguiu remover."""
    remaining = []
    for thumbprint in _pending(audit_root):
        try:
            store.remove(thumbprint)
            notify(f"Certificado {thumbprint[:8]}... de execucao anterior removido do repositorio")
        except Exception:  # noqa: BLE001
            remaining.append(thumbprint)
            notify(f"ATENCAO: remova o certificado {thumbprint[:8]}... pelo certmgr.msc")
    _set_pending(audit_root, remaining)


def cadastrar(config: AppConfig, record: LawyerRecord, pfx: bytes | None, pfx_password: str,
              code_provider: Callable[[str], str | None], audit_root: Path, *,
              rehearsal: bool = False, notify: Notify = print, thumbprint: str = "",
              assist=None, senha_sso: bool = False, senha_sso_ja_cadastrado: bool = False,
              store=None, browser_factory=None, signer_factory=None) -> ResultadoCadastro:
    """Roda o cadastro com o A1. Nao pede senha do PJe: isso e feito depois (credencial).

    pfx = None: usa o certificado ja instalado no Windows com o CPF do advogado (ou o
    `thumbprint` indicado), sem instalar nem remover nada e sem senha do certificado.
    """
    from .certificate import CertificateError, find_installed, read_pfx
    from .config import ConfigurationError
    from .flow import PJeRegistrationFlow

    if store is None:
        from .certificate import WindowsCertificateStore
        store = WindowsCertificateStore()
    if browser_factory is None:
        from .browser import PlaywrightBrowser
        browser_factory = PlaywrightBrowser
    if signer_factory is None:
        from .signer import PJeOfficeSigner

        def signer_factory(cfg):
            return PJeOfficeSigner(cfg.signer, site_hosts=(urlparse(cfg.base_url).hostname or "",))

    chosen_thumbprint = thumbprint
    thumbprint = None  # daqui em diante: o que ESTA execucao instalou e precisa remover
    browser = None
    try:
        if pfx is None:
            info, aviso = find_installed(store, record.cpf_esperado, chosen_thumbprint or None)
            notify(f"Usando o certificado instalado no Windows: emissor {info.emissor}, "
                   f"valido ate {info.valido_ate:%d/%m/%Y}, thumbprint {info.thumbprint_sha1[:8]}...")
            if aviso:
                notify("  aviso: " + aviso)
            pfx_password = ""
        else:
            info = read_pfx(pfx, pfx_password)
        (assert_rehearsal_ready if rehearsal else assert_live_ready)(config, record)
        if pfx is None:
            pass  # ja esta no repositorio: nao instala nem remove
        elif store.contains(info.thumbprint_sha1):
            notify("Certificado ja estava no repositorio do Windows; sera mantido")
        else:
            notify("Instalando o certificado no repositorio do Windows")
            _set_pending(audit_root, _pending(audit_root) + [info.thumbprint_sha1])
            thumbprint = info.thumbprint_sha1
            installed = store.install(pfx, pfx_password)
            if installed != thumbprint:
                _set_pending(audit_root, _pending(audit_root) + [installed])
                thumbprint = installed

        audit_root.mkdir(parents=True, exist_ok=True)
        audit = AuditLog(audit_root)
        audit.on_record = lambda event, state: notify("  " + event.replace("_", " "))
        browser = browser_factory(config)
        signer = signer_factory(config)
        flow = PJeRegistrationFlow(config, record, info, pfx_password, browser, signer, audit,
                                   code_provider=code_provider, rehearsal=rehearsal,
                                   request_password=False, assist=assist)
        result = flow.run()
        # Senha pelo login unico (sem e-mail), aproveitando a sessao do certificado ainda aberta.
        senha, senha_detalhe = "", ""
        quer_senha = senha_sso and not rehearsal and (
            result.status.value == "COMPLETED"
            or (result.status.value == "ALREADY_REGISTERED" and senha_sso_ja_cadastrado))
        if quer_senha:
            from .credencial import set_password_via_sso
            from .senha_cli import _passwords

            try:
                step = set_password_via_sso(browser, config, *_passwords())
                senha, senha_detalhe = (step.value if step.ok else ""), step.detail
            except Exception as exc:  # noqa: BLE001
                senha_detalhe = f"falha na senha pelo login unico ({type(exc).__name__})"
            audit.record("password_set_via_sso" if senha else "password_via_sso_unavailable",
                         result.state.value, {"ok": bool(senha)})
            notify(("  senha pelo login unico: OK - " if senha else "  senha pelo login unico: nao foi - ")
                   + senha_detalhe)
        serialized = asdict(result)
        serialized["status"] = result.status.value
        serialized["state"] = result.state.value
        audit.write_result(serialized)
        return ResultadoCadastro(result.status.value, result.reason, audit.run_id,
                                 senha=senha, senha_detalhe=senha_detalhe)
    except (CertificateError, ConfigurationError) as exc:
        return ResultadoCadastro("ERROR", str(exc))
    except Exception as exc:  # noqa: BLE001
        return ResultadoCadastro("ERROR", f"Falha inesperada: {type(exc).__name__}: {exc}")
    finally:
        pfx_password = ""
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
        if thumbprint:
            try:
                store.remove(thumbprint)
                _set_pending(audit_root, [t for t in _pending(audit_root) if t != thumbprint])
                notify("Certificado removido do repositorio")
            except Exception:  # noqa: BLE001
                notify("ATENCAO: remova o certificado manualmente do repositorio do Windows (certmgr.msc)")
