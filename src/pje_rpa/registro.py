"""Identidade dos tribunais: fonte unica da verdade (tribunais\\_registro.toml).

Cada tribunal tem uma IDENTIDADE permanente, separada do endereco em que o robo navega:

  id                identificador interno (= nome do arquivo tribunais\\<id>.toml). Pedidos, integracoes,
                    historico, regras de credencial e exigencias reconhecem o tribunal por ele.
  nome              nome de exibicao.
  ramo / sigla      TRT, TJ, TRF, TRE, TSE, TST, CNJ / TRT5, TJES...
  grau              "1G", "2G" ou "" (instancia unica).
  grupo_credencial  tribunais que compartilham uma credencial so (os dois graus de cada TRT).
  endereco_padrao   endereco de login de referencia (revisado no Git). So serve para (a) a
                    seguranca: o envio real num endereco diferente dele exige aprovacao explicita
                    (enderecos.py) e (b) reconhecer registros antigos que guardavam o endereco no
                    lugar da identidade (resolver com texto).
  hosts_autorizados hosts em que a navegacao pode comecar (validacao em enderecos.py).

O endereco de navegacao (base_url do .toml) NAO e identidade: muda-lo nao muda o tribunal, o
grau, o grupo da credencial, os pedidos nem o historico. Nenhuma regra de negocio le o endereco.

Um tribunal fora do registro pode declarar a identidade numa secao [tribunal] do proprio .toml
(nao herdada), com os mesmos campos. Pasta sem registro (ex.: config.example.toml na raiz):
identidade = nome do arquivo, sem grau nem grupo, e nenhum host autorizado.
"""
from __future__ import annotations

import re
import tomllib
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ARQUIVO = "_registro.toml"
GRAUS = ("1G", "2G", "")
RAMOS = ("TRT", "TJ", "TRF", "TRE", "TSE", "TST", "CNJ", "STJ", "STF", "TJM", "OUTRO")
CAMPOS = {"id", "nome", "sistema", "ramo", "sigla", "grau", "grupo_credencial", "endereco_padrao",
          "hosts_autorizados"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class RegistroInvalido(ValueError):
    """Registro de tribunais com erro: a mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Tribunal:
    id: str
    nome: str
    ramo: str
    sigla: str = ""
    grau: str = ""
    grupo_credencial: str = ""
    sistema: str = "pje"
    endereco_padrao: str = ""
    hosts_autorizados: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tribunal_id(self) -> str:
        return self.id


# ------------------------------------------------------------------ pasta e leitura
def pasta_padrao() -> Path:
    """tribunais\\ do diretorio atual (como o robo roda na VM) ou, sem ela, a do projeto."""
    for pasta in (Path("tribunais"), Path(__file__).resolve().parents[2] / "tribunais"):
        if (pasta / ARQUIVO).exists():
            return pasta
    return Path("tribunais")


def normalizar_endereco(url: str) -> str:
    """Forma canonica para COMPARAR enderecos (esquema e host em minusculas, sem fragmento)."""
    url = (url or "").strip()
    try:
        partes = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), partes.path, partes.query, ""))


def tribunal_de_dict(dados: dict, origem: str = ARQUIVO) -> Tribunal:
    """Monta e valida um tribunal (do registro ou da secao [tribunal] de um .toml)."""
    erros = problemas_do_tribunal(dados)
    if erros:
        raise RegistroInvalido(f"{origem}: " + "; ".join(erros))
    return Tribunal(
        id=dados["id"], nome=dados["nome"], ramo=dados["ramo"], sigla=dados.get("sigla", ""),
        grau=dados.get("grau", ""), grupo_credencial=dados.get("grupo_credencial", ""),
        sistema=dados.get("sistema", "pje"), endereco_padrao=dados.get("endereco_padrao", ""),
        hosts_autorizados=tuple(h.lower() for h in dados.get("hosts_autorizados", ())))


def problemas_do_tribunal(dados: dict) -> list[str]:
    erros: list[str] = []
    tid = dados.get("id")
    rotulo = f"tribunal '{tid}'" if isinstance(tid, str) and tid else "tribunal sem id"
    desconhecidos = sorted(set(dados) - CAMPOS)
    if desconhecidos:
        erros.append(f"{rotulo}: campo(s) desconhecido(s) {', '.join(desconhecidos)}")
    if not isinstance(tid, str) or not _ID.match(tid):
        erros.append(f"{rotulo}: id invalido (use letras minusculas, numeros e hifen)")
    for campo in ("nome", "ramo"):
        if not isinstance(dados.get(campo), str) or not dados.get(campo, "").strip():
            erros.append(f"{rotulo}: '{campo}' obrigatorio")
    for campo in ("sigla", "grau", "grupo_credencial", "sistema", "endereco_padrao"):
        if campo in dados and not isinstance(dados[campo], str):
            erros.append(f"{rotulo}: '{campo}' deve ser texto")
    if dados.get("ramo") and dados.get("ramo") not in RAMOS:
        erros.append(f"{rotulo}: ramo '{dados.get('ramo')}' desconhecido ({', '.join(RAMOS)})")
    if dados.get("grau", "") not in GRAUS:
        erros.append(f"{rotulo}: grau '{dados.get('grau')}' invalido (use 1G, 2G ou vazio)")
    if dados.get("grupo_credencial") and dados.get("grau") not in ("1G", "2G"):
        erros.append(f"{rotulo}: grupo_credencial exige grau 1G ou 2G")
    hosts = dados.get("hosts_autorizados", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        erros.append(f"{rotulo}: hosts_autorizados deve ser uma lista de textos")
    else:
        from .enderecos import problemas_do_host

        for host in hosts:
            erros += [f"{rotulo}: {p}" for p in problemas_do_host(host.lower())]
        if dados.get("endereco_padrao"):
            from .enderecos import problemas_do_endereco

            erros += [f"{rotulo}: endereco_padrao: {p}"
                      for p in problemas_do_endereco(dados["endereco_padrao"], [h.lower() for h in hosts])]
    return erros


@lru_cache(maxsize=8)
def _carregar(caminho: str, versao: float) -> dict[str, Tribunal]:
    try:
        with open(caminho, "rb") as arquivo:
            bruto = tomllib.load(arquivo)
    except tomllib.TOMLDecodeError as exc:
        raise RegistroInvalido(f"{ARQUIVO}: TOML invalido ({exc})") from exc
    desconhecidas = sorted(set(bruto) - {"tribunal"})
    if desconhecidas:
        raise RegistroInvalido(f"{ARQUIVO}: secao(oes) desconhecida(s): {', '.join(desconhecidas)}")
    lista = bruto.get("tribunal", [])
    if not isinstance(lista, list):
        raise RegistroInvalido(f"{ARQUIVO}: use [[tribunal]] para cada tribunal")
    erros: list[str] = []
    tribunais: dict[str, Tribunal] = {}
    for dados in lista:
        problemas = problemas_do_tribunal(dados) if isinstance(dados, dict) else ["entrada invalida"]
        if problemas:
            erros += problemas
            continue
        t = tribunal_de_dict(dados)
        if t.id in tribunais:
            erros.append(f"tribunal '{t.id}': id repetido")
            continue
        tribunais[t.id] = t
    erros += problemas_dos_grupos(tribunais.values())
    if erros:
        raise RegistroInvalido(f"{ARQUIVO} com {len(erros)} problema(s):\n  - " + "\n  - ".join(erros))
    return tribunais


def problemas_dos_grupos(tribunais) -> list[str]:
    """Cada grupo_credencial tem exatamente um 1o grau e um 2o grau, do mesmo ramo."""
    grupos: dict[str, list[Tribunal]] = {}
    for t in tribunais:
        if t.grupo_credencial:
            grupos.setdefault(t.grupo_credencial, []).append(t)
    erros = []
    for nome, membros in sorted(grupos.items()):
        graus = sorted(m.grau for m in membros)
        if graus != ["1G", "2G"]:
            erros.append(f"grupo_credencial '{nome}': precisa de um 1G e um 2G "
                         f"(tem: {', '.join(m.id + '=' + m.grau for m in membros)})")
        if len({m.ramo for m in membros}) > 1:
            erros.append(f"grupo_credencial '{nome}': membros de ramos diferentes")
    return erros


def carregar(pasta: Path | None = None) -> dict[str, Tribunal] | None:
    """id -> Tribunal. None quando a pasta nao tem registro. Registro com erro: RegistroInvalido."""
    caminho = (pasta or pasta_padrao()) / ARQUIVO
    try:
        versao = caminho.stat().st_mtime_ns
    except OSError:
        return None
    return _carregar(str(caminho.resolve()), versao)


def obter(tribunal_id: str, pasta: Path | None = None) -> Tribunal | None:
    return (carregar(pasta) or {}).get((tribunal_id or "").strip())


def por_endereco_legado(url: str, pasta: Path | None = None) -> Tribunal | None:
    """SO para registros antigos que guardavam o endereco de login no lugar da identidade
    (planilhas de entrega e mapas de integracao anteriores). Compara com o endereco_padrao do registro;
    nao usa padroes de URL. Endereco que nao e o de referencia nao e reconhecido (None)."""
    alvo = normalizar_endereco(url)
    if not alvo:
        return None
    achados = [t for t in (carregar(pasta) or {}).values() if normalizar_endereco(t.endereco_padrao) == alvo]
    return achados[0] if len(achados) == 1 else None


# ------------------------------------------------------------------ resolucao
@dataclass(frozen=True)
class Identidade:
    tribunal_id: str = ""
    grau: str = ""
    grupo_credencial: str = ""
    ramo: str = ""


def identidade(alvo, pasta: Path | None = None) -> Identidade:
    """Identidade de qualquer coisa que represente um tribunal: AppConfig, OpcaoTribunal, Tribunal,
    Identidade ou o id (texto). Texto que nao e id de tribunal e tratado como endereco de um
    registro ANTIGO (por_endereco_legado, com aviso de obsolescencia)."""
    if alvo is None:
        return Identidade()
    if isinstance(alvo, Identidade):
        return alvo
    if isinstance(alvo, str):
        texto = alvo.strip()
        t = obter(texto, pasta) if _ID.match(texto or "-") else None
        if t is None and "://" in texto:
            warnings.warn("tribunal identificado pelo endereco (registro antigo): use o id do tribunal",
                          DeprecationWarning, stacklevel=3)
            t = por_endereco_legado(texto, pasta)
        return Identidade(t.id, t.grau, t.grupo_credencial, t.ramo) if t else Identidade()
    tid = getattr(alvo, "tribunal_id", None) or getattr(alvo, "chave", None) or getattr(alvo, "id", "") or ""
    return Identidade(str(tid), getattr(alvo, "grau", "") or "",
                      getattr(alvo, "grupo_credencial", "") or "", getattr(alvo, "ramo", "") or "")
