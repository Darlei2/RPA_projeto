from pje_rpa.signer import PJE_SSO_HOST, authorization_allowed

HOSTS = {PJE_SSO_HOST, "pje.tjap.jus.br"}
ASK = "A aplica\u00e7\u00e3o 'PJe' deseja acessar o PJeOffice."


def _labels(address):
    return [ASK, f"Endere\u00e7o: {address}", "Deseja autorizar?"]


def test_autoriza_tribunal_configurado_e_sso():
    assert authorization_allowed(_labels("https://pje.tjap.jus.br/1g"), HOSTS)
    assert authorization_allowed(_labels("https://sso.cloud.pje.jus.br/auth/realms/pje"), HOSTS)


def test_recusa_enderecos_estranhos():
    assert not authorization_allowed(_labels("https://pje.tjpa.jus.br/pje"), HOSTS)
    assert not authorization_allowed(_labels("http://pje.tjap.jus.br/1g"), HOSTS)
    assert not authorization_allowed(_labels("https://pje.tjap.jus.br.evil.com/1g"), HOSTS)
    assert not authorization_allowed(["Endere\u00e7o: https://pje.tjap.jus.br/1g"], HOSTS)
