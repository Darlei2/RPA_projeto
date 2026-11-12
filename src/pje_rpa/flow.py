from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from .audit import AuditLog
from .browser import BrowserError, BrowserPort, SimulatedBrowser
from .certificate import CertificateInfo
from .identidade import nomes_compativeis
from .models import AppConfig, FlowState, LawyerRecord, RunResult, RunStatus, digits
from .signer import SignerError, SignerPort

# Tempo maximo para o PDPJ sair da tela de login (ou do 2FA) depois da acao do usuario/PJeOffice.
LOGIN_TIMEOUT_MS = 90_000

# Recebe a mensagem a exibir e devolve o codigo do segundo fator (ou None se expirar).
CodeProvider = Callable[[str], "str | None"]


class PJeRegistrationFlow:
    """Cadastro automatico usando o PJeOffice PRO instalado no computador.

    Paradas que continuam existindo:
    - CAPTCHA ou bloqueio de acesso: encerra (nao ha tentativa de contorno);
    - segundo fator: pede o codigo ao operador pela pagina local;
    - divergencia de nome/CPF/OAB entre certificado, dados e PJe: encerra antes de enviar.
    """

    def __init__(
        self,
        config: AppConfig,
        record: LawyerRecord,
        certificate: CertificateInfo,
        certificate_password: str,
        browser: BrowserPort,
        signer: SignerPort,
        audit: AuditLog,
        code_provider: CodeProvider,
        rehearsal: bool = False,
        request_password: bool = True,
        assist: Callable[[dict], str] | None = None,
    ) -> None:
        self.config = config
        self.record = record
        self.certificate = certificate
        self._password = certificate_password
        self.browser = browser
        self.signer = signer
        self.audit = audit
        self.code_provider = code_provider
        # Ensaio: roda no PJe real, mas para antes de "Confirmar dados" (nada e enviado nem assinado).
        self.rehearsal = rehearsal
        # Execucao em lote (lote.py) cuida da senha com a caixa de e-mail: aqui so cadastra.
        self.request_password = request_password
        self._sem_dado: list[str] = []
        # Modo assistido (lote): recebe o relatorio do formulario e devolve
        # "reavaliar", "seguir" ou "parar". None = sem operador (para para revisao).
        self.assist = assist
        self.state = FlowState.START

    def run(self) -> RunResult:
        try:
            return self._run()
        except (BrowserError, SignerError) as exc:
            return self._failed(str(exc))
        except Exception as exc:
            self.audit.record("unexpected_error", self.state.value, {"type": type(exc).__name__})
            return self._failed(f"Erro inesperado: {type(exc).__name__}")
        finally:
            self._password = ""

    def _run(self) -> RunResult:
        mismatch = self._certificate_mismatch()
        if mismatch:
            return self._blocked(mismatch)

        self._transition(FlowState.LOGIN, "open_login", {"tribunal": self.config.tribunal})
        self.browser.open(self.config.base_url)
        if blocked := self._blocked_reason():
            return self._blocked(blocked)
        # Tribunais com login centralizado (PDPJ/SSO) exigem um clique antes da tela do certificado.
        if "login_sso_button" in self.config.selectors and self.browser.visible("login_sso_button", 5000):
            self.browser.click("login_sso_button")
            self.audit.record("sso_login_opened", self.state.value)
        if not self.browser.visible("login_certificate_button", 10000):
            return self._failed("Botao de certificado digital nao localizado")

        self.browser.click("login_certificate_button")
        self._transition(FlowState.WAITING_CERTIFICATE, "certificate_requested")
        if hasattr(self.signer, '_login_complete'):
            self.signer._login_complete = lambda: (self.browser.visible('second_factor', 200) or self.browser.visible('lawyer_panel', 200) or self.browser.visible('registration_form', 200))
        self.signer.authenticate(self.certificate, self._password)
        self.audit.record("signer_authenticated", self.state.value)

        # Depois do OK, o PJeOffice ainda assina e envia o desafio ao PDPJ (medido: ~20 s).
        # Fechar o navegador antes disso aborta o login, entao espera a tela de login sumir.
        if not self.browser.wait_hidden("login_certificate_button", LOGIN_TIMEOUT_MS):
            if blocked := self._blocked_reason():
                return self._blocked(blocked)
            return self._failed("Login por certificado nao concluiu: o PJe continuou na tela de login")
        self.audit.record("login_page_left", self.state.value)

        if blocked := self._post_login_blocked():
            return self._blocked(blocked)

        if self.browser.visible("second_factor", 8000):
            self._transition(FlowState.SECOND_FACTOR, "second_factor_detected")
            code = self.code_provider(
                "O PJe pediu o codigo de segundo fator, enviado ao e-mail/celular do advogado."
            )
            if not code:
                return self._needs_human("Codigo de segundo fator nao informado a tempo")
            self.browser.fill("second_factor_input", code.strip())
            try:
                self.browser.click("second_factor_submit")
            except Exception:
                # A tela do 2FA pode enviar sozinha ao completar o codigo: o botao some no meio do
                # clique. Se o campo do codigo ja saiu da tela, o login seguiu.
                if self.browser.visible("second_factor_input", 1000):
                    raise
            if isinstance(self.browser, SimulatedBrowser):
                self.browser.human_completed_second_factor()
            if not self.browser.wait_hidden("second_factor_input", LOGIN_TIMEOUT_MS):
                return self._needs_human("Codigo de segundo fator recusado ou expirado")
            self.audit.record("second_factor_accepted", self.state.value)
            if blocked := self._post_login_blocked():
                return self._blocked(blocked)

        if self.browser.visible("lawyer_panel", 2500):
            self._transition(FlowState.ALREADY_REGISTERED, "existing_profile_detected")
            # Ja cadastrado e ainda logado com o A1: alinha o e-mail do PJe ao da planilha (para o
            # link de senha chegar na caixa que controlamos), na mesma sessao. Best-effort.
            self._garantir_email_cadastral()
            return self._result(RunStatus.ALREADY_REGISTERED, "Advogado ja cadastrado neste PJe")

        if not self.browser.visible("registration_form", 10000):
            # Execucao anterior ja enviou o formulario e parou no termo: retoma dali.
            if self.browser.visible("commitment_term", 3000):
                self._transition(FlowState.COMMITMENT_TERM, "commitment_term_resumed",
                                 {"autorizacao_ref": self.record.autorizacao_ref})
                if self.rehearsal:
                    return self._result(
                        RunStatus.REHEARSAL_OK,
                        "Ensaio: o PJe ja esta no termo de compromisso; nada foi assinado",
                    )
                return self._sign_commitment()
            return self._failed("Formulario de cadastro nao localizado apos autenticacao")
        self._transition(FlowState.REGISTRATION_FORM, "registration_form_detected")
        if hasattr(self.browser, "page"):
            if (stop := self._adaptive_form()) is not None:
                return stop
        else:
            if mismatch := self._identity_mismatch():
                return self._blocked(mismatch)

            self._fill_form()
        self._transition(FlowState.REVIEW, "form_filled", self.record.safe_summary())

        # Botao de avancar: seletor homologado ou, se nao houver, o botao reconhecido
        # pelo texto (cada PJe usa um rotulo). O ensaio tambem valida esta etapa.
        for attempt in range(1, 4):
            advance = self._locate_action("confirm_data", "advance")
            if advance is None or "ambiguous" in advance:
                return self._failed(self._action_problem("avancar o formulario", advance))
            self.audit.record("advance_button_located", self.state.value,
                              {"text": advance["text"], "source": advance["source"]})

            if self.rehearsal:
                self.audit.record("rehearsal_stopped_before_submit", self.state.value)
                aviso = ""
                if self._sem_dado:
                    aviso = (". ATENCAO: campo(s) sem dado na planilha (o PJe pode exigir ao enviar): "
                             + ", ".join(self._sem_dado))
                return self._result(
                    RunStatus.REHEARSAL_OK,
                    f"Ensaio concluido: o robo clicaria em '{advance['text']}'. "
                    "Nada foi enviado nem assinado" + aviso,
                )

            self._click_action("confirm_data", "advance", advance)
            self.audit.record("advance_button_clicked", self.state.value, {"text": advance["text"]})
            if blocked := self._blocked_reason():
                return self._blocked(blocked)
            if self.browser.visible("commitment_term", 15000):
                break
            messages = self._page_messages()
            # O PJe devolveu o formulario (ex.: TRT: "campo obrigatorio" em RG e confirmacao de
            # e-mail): reavalia, preenche o que tiver dado e, no modo assistido, pausa para o resto.
            if (attempt < 3 and hasattr(self.browser, "page")
                    and self.browser.visible("registration_form", 3000)):
                self.audit.record("form_returned_by_pje", self.state.value,
                                  {"attempt": attempt, "messages": messages})
                if (stop := self._adaptive_form(after_submit=True)) is not None:
                    return stop
                continue
            if messages:
                return self._needs_human("O PJe recusou o formulario: " + " | ".join(messages))
            return self._failed("Termo de compromisso nao localizado apos avancar o formulario")
        self._transition(FlowState.COMMITMENT_TERM, "commitment_term_detected",
                         {"autorizacao_ref": self.record.autorizacao_ref})
        return self._sign_commitment()

    def _adaptive_form(self, after_submit: bool = False) -> RunResult | None:
        """Avalia e preenche o formulario; None = pode avancar, senao o resultado da parada."""
        from .adaptive_form import operate

        # Campos fixados no .toml do tribunal: campo_uf = ["id=..."] / ["name=..."] / ["label=..."]
        overrides = {k[len("campo_"):]: v for k, v in self.config.selectors.items() if k.startswith("campo_")}
        rounds = 0
        while True:
            report = operate(self.browser.page, self.record, **({"overrides": overrides} if overrides else {}))
            self._preserve_address(report)
            if report.get("unverified") and not self.rehearsal:
                report["pending"].extend(
                    "verificacao_obrigatoria:" + key
                    for key in report["unverified"]
                )
            self.audit.record("adaptive_form_evaluated", self.state.value, report)

            if report["blocked"]:
                # Identidade divergente nunca e liberada pelo modo assistido.
                return self._blocked("Divergencia encontrada: " + ", ".join(report["blocked"]))
            if not report["pending"]:
                break

            # Modo assistido: o operador resolve no Edge aberto e o robo reavalia, ou confirma
            # que o formulario esta certo e o robo segue. Sem operador: para para revisao.
            decision = self.assist(report) if (self.assist and rounds < 5) else "parar"
            rounds += 1
            if decision == "reavaliar":
                self.audit.record("operator_fixed_form", self.state.value, {"round": rounds})
                continue
            if decision == "seguir":
                self.audit.record("operator_confirmed_form", self.state.value,
                                  {"pending_accepted": report["pending"]})
                break
            self._transition(FlowState.REVIEW, "adaptive_review_pending", report)
            return self._needs_human("Revisao necessaria: " + ", ".join(report["pending"]))
        self._sem_dado = list(report.get("sem_dado", []))
        if after_submit and rounds == 0 and not report["filled"]:
            # Nada novo para preencher e nada pendente: o PJe recusou por outro motivo.
            messages = self._page_messages()
            self._capture("falha")
            return self._needs_human("O PJe devolveu o formulario sem indicar o campo"
                                     + (": " + " | ".join(messages) if messages else ""))
        return None

    def _sign_commitment(self) -> RunResult:
        sign = self._locate_action("sign_term", "sign")
        if sign is None or "ambiguous" in sign:
            return self._failed(self._action_problem("assinar o termo", sign))
        self._click_action("sign_term", "sign", sign)
        self.audit.record("sign_button_clicked", self.state.value, {"text": sign["text"]})
        if hasattr(self.signer, "_sign_complete"):
            self.signer._sign_complete = lambda: self.browser.visible("completion", 200)
        try:
            self.signer.sign(self.certificate, self._password)
            self.audit.record("term_signed_via_pjeoffice", self.state.value)
        except SignerError:
            # Sem janela do PJeOffice, mas o PJe ja concluiu: a assinatura foi aceita.
            if not self.browser.visible("completion", 5000):
                raise
            self.audit.record("term_signed_without_dialog", self.state.value)

        if not self.browser.visible("completion", 15000):
            blocked = self._blocked_reason()
            return self._blocked(blocked) if blocked else self._failed("Conclusao do cadastro nao confirmada")
        self._transition(FlowState.COMPLETED, "registration_completed")
        reason = "Cadastro concluido e confirmado pela tela final"
        if self.request_password and hasattr(self.browser, "page"):
            reason += ". " + self._request_password_link()
        return self._result(RunStatus.COMPLETED, reason)

    def _garantir_email_cadastral(self) -> None:
        """Ja cadastrado: confere o e-mail no 'Alteracao de dados cadastrais' e, se divergir do da
        planilha, troca (na sessao ja aberta, sem reabrir o PJe). Qualquer falha so e registrada e o
        fluxo segue - cai no comportamento de hoje (link de senha pode ir para o e-mail antigo)."""
        if self.rehearsal or not getattr(self.record, "email", "") or not hasattr(self.browser, "page"):
            return
        try:
            from .credencial import ajustar_email_cadastral
            ok, detalhe = ajustar_email_cadastral(self.browser.page, self.config.base_url, self.record.email)
            self.audit.record("email_cadastral_ajustado" if ok else "email_cadastral_sem_ajuste",
                              self.state.value, {"detalhe": detalhe})
        except Exception as exc:  # noqa: BLE001 - cadastro ja concluido; nao pode quebrar o fluxo
            self.audit.record("email_cadastral_falhou", self.state.value, {"erro": type(exc).__name__})

    def _request_password_link(self) -> str:
        """Sem senha cadastrada a credencial nao serve (so certificado/jus postulandi):
        sai do PJe e pede o link de criacao de senha para o e-mail cadastrado."""
        from .credencial import request_password_link

        try:
            step = request_password_link(self.browser, self.config,
                                         self.record.cpf_esperado, self.record.email)
        except Exception as exc:  # noqa: BLE001 - o cadastro ja esta concluido
            step = None
            detail = f"Falha ao pedir o link de senha: {type(exc).__name__}"
        else:
            detail = self._scrub(step.detail) if not step.ok else step.detail
        event = "password_link_requested" if step and step.ok else "password_link_request_failed"
        self.audit.record(event, self.state.value, {"detail": detail})
        if step and step.ok:
            return ("Link de criacao de senha enviado ao e-mail cadastrado: "
                    "rode scripts\\criar-senha.ps1 para cadastrar a senha e testar o login")
        return ("ATENCAO: " + detail + ". Peca 'Esqueceu a senha?' manualmente ou rode "
                "scripts\\criar-senha.ps1")

    def _locate_action(self, group: str, purpose: str) -> dict | None:
        if self.browser.visible(group, 2500):
            return {"text": f"seletor homologado {group}", "source": "config"}
        if not hasattr(self.browser, "page"):
            return None
        from .adaptive_form import locate_action

        try:
            return locate_action(self.browser.page, purpose)
        except Exception:
            return None

    def _click_action(self, group: str, purpose: str, found: dict) -> None:
        if found["source"] == "config":
            self.browser.click(group)
            return
        from .adaptive_form import click_action

        click_action(self.browser.page, purpose)

    def _page_messages(self) -> list[str]:
        if not hasattr(self.browser, "page"):
            return []
        from .adaptive_form import page_messages

        return [self._scrub(m) for m in page_messages(self.browser.page)]

    @staticmethod
    def _action_problem(what: str, found: dict | None) -> str:
        if found and "ambiguous" in found:
            return (f"Mais de um botao possivel para {what}: "
                    + ", ".join(found["ambiguous"])
                    + ". Homologue o seletor no .toml do tribunal")
        return f"Botao para {what} nao localizado. Homologue o seletor no .toml do tribunal"

    def _certificate_mismatch(self) -> str | None:
        # O CPF e a ancora de identidade (conferido abaixo). O nome do certificado as vezes vem
        # sem um sobrenome (ex.: "ANA CAROLINA DE JESUS" vs "...DE JESUS ANDRADE"): aceita quando
        # um nome e subsequencia, na ordem, do outro; ainda barra quando for pessoa diferente.
        if not nomes_compativeis(self.certificate.nome, self.record.nome_esperado):
            return "Nome do certificado diferente do nome informado"
        if self.certificate.cpf != self.record.cpf_esperado:
            return "CPF do certificado diferente do CPF informado"
        self.audit.record("certificate_validated", self.state.value, self.certificate.safe_summary())
        return None

    def _fill_form(self) -> None:
        address = self.record.endereco
        fields = {
            "email": self.record.email,
            "phone": self.record.telefone,
            "cep": address.cep,
            "logradouro": address.logradouro,
            "address_number": address.numero,
            "complemento": address.complemento,
            "bairro": address.bairro,
            "cidade": address.cidade,
            "uf": address.uf,
        }
        for group, value in fields.items():
            if value and self.browser.visible(group, 800):
                self.browser.fill(group, value)
        self.audit.record("fields_filled", self.state.value, {"fields": sorted(k for k, v in fields.items() if v)})

    def _identity_mismatch(self) -> str | None:
        detected_name = self.browser.read("detected_name")
        detected_cpf = self.browser.read("detected_cpf")
        detected_oab = self.browser.read("detected_oab")
        if not nomes_compativeis(detected_name, self.record.nome_esperado):
            return "Divergencia entre o nome esperado e o retornado pelo PJe"
        if digits(detected_cpf) != self.record.cpf_esperado:
            return "Divergencia entre o CPF esperado e o retornado pelo PJe"
        detected_oab_digits = digits(detected_oab)
        detected_oab_letters = re.sub(r"[^A-Z]", "", detected_oab.upper()).replace("OAB", "")
        if detected_oab_digits != self.record.oab_numero or self.record.oab_uf not in detected_oab_letters:
            return "Divergencia entre a OAB esperada e a retornada pelo PJe"
        self.audit.record("identity_validated", self.state.value, self.record.safe_summary())
        return None

    def _post_login_blocked(self) -> str | None:
        """Depois do login: no painel/Quadro de Avisos de quem ja e usuario, os avisos do tribunal
        podem citar "jus postulandi" (ex.: TRF6). Ali esse texto nao indica o perfil da pessoa."""
        on_panel = self.browser.visible("lawyer_panel", 1500)
        if on_panel:
            self.audit.record("existing_panel_seen", self.state.value)
        return self._blocked_reason(skip=("jus_postulandi",) if on_panel else ())

    def _blocked_reason(self, skip: tuple[str, ...] = ()) -> str | None:
        labels = {
            "captcha": "CAPTCHA detectado; nenhuma tentativa de contorno foi realizada",
            "certificate_error": "Erro ou bloqueio de certificado detectado",
            "access_blocked": "Bloqueio de acesso detectado",
            "jus_postulandi": "PJe nao reconheceu o perfil como advogado e indicou Jus Postulandi",
        }
        for group, reason in labels.items():
            if group in skip:
                continue
            marker = self.browser.page_text_contains(self.config.stop_markers.get(group, []))
            if marker:
                self.audit.record("stop_marker_detected", self.state.value, {"group": group, "marker": marker})
                return reason
        return None

    def _transition(self, state: FlowState, event: str, details: dict | None = None) -> None:
        self.state = state
        self.audit.record(event, state.value, details)

    @staticmethod
    def _preserve_address(report: dict) -> None:
        """Endereco que ja existe no PJe e diverge da planilha: preserva e registra (nao trava)."""
        prefix = "valor_preexistente_divergente:"
        allowed = {"logradouro", "complemento"}
        remaining, preserved_address = [], []
        for issue in report.get("pending", []):
            # "Municipio/UF" e texto fixo do PJe: o robo nao consegue alterar.
            if issue == "municipio_uf_divergente":
                preserved_address.append("municipio_uf")
                continue
            if isinstance(issue, str) and issue.startswith(prefix) and issue[len(prefix):] in allowed:
                preserved_address.append(issue[len(prefix):])
                continue
            remaining.append(issue)
        if preserved_address:
            report["pending"] = remaining
            report.setdefault("preserved", []).extend(preserved_address)
            report["address_divergences_preserved"] = sorted(set(preserved_address))

    def _blocked(self, reason: str) -> RunResult:
        self._capture("bloqueio")
        self._transition(FlowState.BLOCKED, "flow_blocked", {"reason": reason})
        return self._result(RunStatus.BLOCKED, reason)

    def _capture(self, tag: str) -> None:
        """Print da tela na pasta da auditoria (runtime\\audit\\<execucao>, fora do Git) para
        entender por que o fluxo parou. Fica so nesta maquina."""
        page = getattr(self.browser, "page", None)
        if page is None:
            return
        name = f"tela-{tag}.png"
        try:
            page.screenshot(path=str(self.audit.run_dir / name), full_page=True)
        except Exception:  # noqa: BLE001 - sem print, segue com o resto do diagnostico
            return
        self.audit.record("screen_captured", self.state.value, {"file": name})

    def _failed(self, reason: str) -> RunResult:
        self._capture("falha")
        details: dict = {"reason": reason}
        # Registra a estrutura da tela (titulos, rotulos, botoes; nunca valores digitados,
        # numeros e nome mascarados) para entender e homologar a etapa que falhou.
        try:
            details["page"] = self._scrub(self.browser.outline())
        except Exception:  # noqa: BLE001
            pass
        self._transition(FlowState.FAILED, "flow_failed", details)
        return self._result(RunStatus.FAILED, reason)

    def _scrub(self, value):
        """Mascara nome do titular e sequencias numericas (CPF, OAB, telefone) em textos da tela."""
        if isinstance(value, dict):
            return {k: v if k == "url" else self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        if not isinstance(value, str):
            return value
        text = re.sub(r"\d[\d.\-/ ]{3,}\d", "***", value)
        for part in _normalize_name(self.record.nome_esperado).split():
            if len(part) > 2:
                text = re.sub(re.escape(part), "***", _normalize_name(text), flags=re.IGNORECASE)
        return text

    def _needs_human(self, reason: str) -> RunResult:
        self._capture("revisao")
        details: dict = {"reason": reason}
        try:
            details["page"] = self._scrub(self.browser.outline())
        except Exception:  # noqa: BLE001
            pass
        self.audit.record("human_action_pending", self.state.value, details)
        return self._result(RunStatus.NEEDS_HUMAN, reason)

    def _result(self, status: RunStatus, reason: str) -> RunResult:
        return RunResult(
            run_id=self.audit.run_id,
            status=status,
            state=self.state,
            reason=reason,
            details={"tribunal": self.config.tribunal},
        )


def console_code_provider(message: str) -> str | None:
    print(f"\nSEGUNDO FATOR\n{message}\n")
    code = input("Codigo (Enter vazio cancela): ").strip()
    return code or None


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.upper().split())
