from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .audit import AuditLog, verify_audit_file
from .browser import SimulatedBrowser
from .config import ConfigurationError, assert_live_ready, load_config, load_lawyer
from .flow import PJeRegistrationFlow, console_code_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RPA assistido para cadastro inicial de advogado no PJe")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Valida configuracao e cadastro sem abrir o navegador")
    _common_files(validate)

    simulate = sub.add_parser("simulate", help="Executa o fluxo sem acessar o PJe")
    _common_files(simulate)
    simulate.add_argument(
        "--scenario",
        choices=["success", "captcha", "jus_postulandi", "second_factor", "already_registered"],
        default="success",
    )
    simulate.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))
    simulate.add_argument("--ensaio", action="store_true", help="Para antes de confirmar os dados")

    live = sub.add_parser("live", help="Executa no dominio oficial configurado")
    _common_files(live)
    live.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))

    verify = sub.add_parser("verify-audit", help="Verifica a integridade encadeada de um audit.jsonl")
    verify.add_argument("path", type=Path)

    serve = sub.add_parser("serve", help="Abre a pagina local de upload do A1 (modo real)")
    serve.add_argument("--config", type=Path, default=Path("config.toml"))
    serve.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8723)
    serve.add_argument("--ensaio", action="store_true",
                       help="Entra no PJe real e confere os dados, mas para antes de enviar/assinar")

    probe = sub.add_parser("sondar", help="Abre a tela de login do tribunal e confere os seletores (sem certificado)")
    probe.add_argument("--config", type=Path, nargs="+", required=True)

    password = sub.add_parser("senha", help="Pede o link de senha, cadastra a senha e testa CPF + senha + 2FA")
    password.add_argument("--config", type=Path, nargs="+", required=True)
    password.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))
    password.add_argument("--saida", type=Path, default=Path("entregas"),
                          help="Pasta da planilha de entrega (protegida por senha)")

    run = sub.add_parser("executar", help="Fluxo inteiro (cadastro + senha + teste) para os advogados de uma planilha")
    run.add_argument("--tribunal", default="", help="Um ou varios: tjap ou tremg,tjpa,trt5 (sem ele, o robo pergunta)")
    run.add_argument("--planilha", default="", help="Ex.: planilhas\\advogados.xlsx (sem ela, o robo pergunta)")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--ensaio", action="store_true", default=None, help="Para antes de enviar (nada e assinado)")
    mode.add_argument("--real", action="store_true", default=None, help="Cadastro real (tribunal precisa estar liberado)")
    run.add_argument("--modelo", action="store_true", help="So cria a planilha modelo em planilhas\\advogados.xlsx")
    run.add_argument("--so-senha", action="store_true",
                     help="Advogado ja cadastrado: sem login com certificado, so nova senha + teste")
    run.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))
    run.add_argument("--saida", type=Path, default=Path("entregas"))

    report = sub.add_parser("relatorio", help="Resume os resultados por tribunal e confere a meta de validacao")
    report.add_argument("--audit-root", type=Path, default=Path("runtime/audit"))
    report.add_argument("--meta", type=int, default=5, help="Minimo de tribunais com cadastro real")
    report.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect-signer", help="Lista os controles das janelas do PJeOffice abertas")
    inspect.add_argument("--config", type=Path, default=Path("config.toml"))
    return parser


def _common_files(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--data", type=Path, default=Path("cadastro.json"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify-audit":
            ok, message = verify_audit_file(args.path)
            print(json.dumps({"valid": ok, "message": message}, ensure_ascii=False))
            return 0 if ok else 2

        if args.command == "serve":
            if args.host not in {"127.0.0.1", "localhost", "::1"}:
                raise ConfigurationError("A pagina local so pode escutar em 127.0.0.1")
            from .webapp import RegistrationServer
            RegistrationServer(args.config, args.audit_root, args.host, args.port,
                               rehearsal=args.ensaio).serve_forever()
            return 0
        if args.command == "sondar":
            from .tribunais import sondar
            failures = 0
            for path in args.config:
                result = sondar(load_config(path))
                result["arquivo"] = str(path)
                failures += 0 if result.get("ok") else 1
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if not failures else 4
        if args.command == "senha":
            from .senha_cli import run_password_wizard
            return run_password_wizard([load_config(p) for p in args.config], args.audit_root, args.saida)
        if args.command == "executar":
            from .lote import PLANILHA_PADRAO, run_executar
            if args.modelo:
                from .planilha import gravar_modelo
                destino = Path(args.planilha) if args.planilha else PLANILHA_PADRAO
                if destino.exists():
                    print(f"Ja existe: {destino} (nao foi sobrescrita)")
                else:
                    print(f"Planilha modelo criada: {gravar_modelo(destino)}")
                return 0
            ensaio = True if args.ensaio else (False if args.real else None)
            return run_executar(args.audit_root, args.saida, args.tribunal, args.planilha, ensaio,
                                so_senha=args.so_senha)
        if args.command == "relatorio":
            from .tribunais import format_relatorio, relatorio
            data = relatorio(args.audit_root, args.meta)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else format_relatorio(data))
            return 0 if data["meta"]["atingida"] else 5
        if args.command == "inspect-signer":
            from .signer import dump_signer_windows
            print(dump_signer_windows(load_config(args.config).signer))
            return 0

        config = load_config(args.config)
        record = load_lawyer(args.data)
        if args.command == "validate":
            print(json.dumps({"valid": True, "tribunal": config.tribunal, "data": record.safe_summary()}, ensure_ascii=False, indent=2))
            return 0

        if args.command != "simulate":
            raise ConfigurationError("O modo real agora roda pela pagina local: use 'pje-rpa serve'")

        args.audit_root.mkdir(parents=True, exist_ok=True)
        audit = AuditLog(args.audit_root)
        from .browser import SimulatedBrowser
        from .certificate import CertificateInfo
        from .signer import SimulatedSigner
        from datetime import datetime, timezone
        browser = SimulatedBrowser(record, scenario=args.scenario)
        info = CertificateInfo(record.nome_esperado, record.cpf_esperado, "AC SIMULADA",
                               datetime(2099,1,1,tzinfo=timezone.utc), "0"*40)
        signer = SimulatedSigner(browser)

        try:
            flow = PJeRegistrationFlow(config, record, info, "simulada", browser, signer, audit,
                                       code_provider=lambda msg: "000000", rehearsal=args.ensaio)
            result = flow.run()
            serialized = asdict(result)
            serialized["status"] = result.status.value
            serialized["state"] = result.state.value
            result_path = audit.write_result(serialized)
            print(json.dumps({**serialized, "result_path": str(result_path)}, ensure_ascii=False, indent=2))
            return 0 if result.status.value in {"COMPLETED", "ALREADY_REGISTERED", "REHEARSAL_OK"} else 3
        finally:
            if browser is not None:
                browser.close()
    except (ConfigurationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERRO DE CONFIGURACAO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

