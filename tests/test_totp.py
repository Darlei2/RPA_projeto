"""Testes sem chaves privadas reais; vetor oficial RFC 6238."""
import base64
import unittest

from pje_rpa.totp import gerar_totp, normalizar_chave


class TOTPTests(unittest.TestCase):
    def test_rfc_6238_sha1(self):
        # Vetor RFC 6238: ASCII 12345678901234567890, T=59 -> 94287082 (8 digitos)
        chave = base64.b32encode(b"12345678901234567890").decode("ascii")
        self.assertEqual(gerar_totp(chave, agora=59, digitos=8), "94287082")
        self.assertEqual(gerar_totp(chave, agora=59), "287082")

    def test_entrada_opcional(self):
        self.assertEqual(normalizar_chave(""), "")
        self.assertEqual(normalizar_chave(None), "")

    def test_normaliza_espacos(self):
        chave = base64.b32encode(b"12345678901234567890").decode("ascii")
        self.assertEqual(normalizar_chave(" " + chave.lower() + " "), chave)

    def test_rejeita_chars_invalidos_sem_expor_valor(self):
        with self.assertRaises(ValueError) as err:
            normalizar_chave("ABC!@#SECRET")
        self.assertNotIn("SECRET", str(err.exception))


if __name__ == "__main__":
    unittest.main()
