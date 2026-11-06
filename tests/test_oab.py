import pytest


@pytest.mark.parametrize("label", [
    "12345 (Advogado)",
    "OAB 12345 (Advogado)",
    "12.345 - Advogado",
    "012345 (ADVOGADO)",
])
def test_oab_sem_uf_com_texto_extra_segue(label):
    from pje_rpa.adaptive_form import oab_check
    assert oab_check(label, "12345", "AP") == "uf_nao_exibida"


@pytest.mark.parametrize("label", [
    "AP12345 (Advogado)",
    "12345/AP (Advogado)",
    "OAB/AP 12345 - Advogado",
    "12345-AP",
])
def test_oab_com_uf_colada_ou_separada(label):
    from pje_rpa.adaptive_form import oab_check
    assert oab_check(label, "12345", "AP") == "ok"


def test_oab_divergencias_continuam_bloqueando():
    from pje_rpa.adaptive_form import oab_check, same
    assert oab_check("12345/PA (Advogado)", "12345", "AP") == "uf_divergente"
    assert oab_check("54321 (Advogado)", "12345", "AP") == "numero_divergente"
    assert same("oab", "12345 (Advogado)", "12345/AP")
    assert not same("oab", "12345/PA", "12345/AP")
