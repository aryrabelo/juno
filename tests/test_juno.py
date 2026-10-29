"""Testes das partes puras do juno.py (sem microfone e sem apertar teclas).

Rodar: .venv/bin/python -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import juno  # noqa: E402


class ShortcutTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(juno.shortcut_script("option+command+."), "key code 47 using {command down, option down}")

    def test_aliases(self):
        self.assertEqual(juno.shortcut_script("ctrl+opt+."), "key code 47 using {control down, option down}")

    def test_letter(self):
        self.assertEqual(juno.shortcut_script("cmd+k"), 'keystroke "k" using {command down}')

    def test_unknown(self):
        with self.assertRaises(SystemExit):
            juno.shortcut_script("hyper+.")


class AgentsTest(unittest.TestCase):
    def test_clean(self):
        agents = juno.clean_agents({"Vendas": "Vendas · OPS", "duas palavras": "X", "vazio": ""}, "teste")
        self.assertEqual(agents, {"vendas": "Vendas · OPS"})

    def test_legacy_tsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agentes.tsv"
            path.write_text("# comentário\npessoal\tPessoal · OPS\nsem-tab\n", encoding="utf-8")
            self.assertEqual(juno.read_legacy_agents(path), {"pessoal": "Pessoal · OPS"})


class RmsTest(unittest.TestCase):
    def test_rms(self):
        self.assertEqual(juno.rms(b""), 0)
        self.assertEqual(juno.rms((16).to_bytes(2, "little", signed=True) * 100), 16)


if __name__ == "__main__":
    unittest.main()
