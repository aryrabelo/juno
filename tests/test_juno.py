"""Testes das partes puras do juno.py (sem microfone e sem apertar teclas).

Rodar: .venv/bin/python -m unittest discover -s tests
"""
import json
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


class WordListTest(unittest.TestCase):
    def test_string_or_list(self):
        self.assertEqual(juno._word_list("Atlas"), ["atlas"])
        self.assertEqual(juno._word_list(["atlas", "Ata"]), ["atlas", "ata"])


class ExampleConfigTest(unittest.TestCase):
    def test_one_example_per_language_with_the_same_keys(self):
        examples = {lang: json.loads((juno.HERE / f"config.example.{lang}.json").read_text(encoding="utf-8"))
                    for lang in juno.MODELS}
        for lang, config in examples.items():
            self.assertEqual(config["language"], lang)
        self.assertEqual(set(examples["pt"]), set(examples["en"]))


class WaitPasteTest(unittest.TestCase):
    def wait(self, before, after):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "Fluid.log"
            log.write_bytes(before)
            since = log.stat().st_size
            log.write_bytes(after)
            old, juno.FLUID_LOG, juno.PASTE_TIMEOUT_S = juno.FLUID_LOG, log, 0.3
            try:
                return juno.wait_paste(since)
            finally:
                juno.FLUID_LOG, juno.PASTE_TIMEOUT_S = old, 10.0

    def test_inserted_without_paste_verify(self):  # FluidVoice 1.6.12
        self.assertEqual(self.wait(b"x" * 10, b"x" * 10 + b"PIPELINE_SUMMARY outcome=inserted\n"), "pasted")

    def test_history_only(self):
        self.assertEqual(self.wait(b"x" * 10, b"x" * 10 + b"PIPELINE_SUMMARY outcome=history_only\n"), "no_field")

    def test_nothing_transcribed(self):  # só "câmbio": o Spoken Send tira a palavra e não sobra texto
        line = b"[03:34:51.745] DICTATION_SUMMARY asrMs=44 readyMs=54 slowest=asr outcome=empty\n"
        self.assertEqual(self.wait(b"x" * 10, b"x" * 10 + line), "empty")

    def test_log_rotated(self):
        self.assertEqual(self.wait(b"x" * 500, b"PASTE_VERIFY confirmed\n"), "pasted")


class ParseCommandTest(unittest.TestCase):
    def test_next_agent_either_order(self):
        expected = f"{juno.AGENT_WORD} {juno.NEXT_WORD}"
        self.assertEqual(juno.parse_command([juno.AGENT_WORD, juno.NEXT_WORD], {}), expected)
        self.assertEqual(juno.parse_command([juno.NEXT_WORD, juno.AGENT_WORD], {}), expected)

    def test_next_agent_only_on_its_own(self):
        self.assertIsNone(juno.parse_command(["[unk]", juno.NEXT_WORD, juno.AGENT_WORD], {}))
        self.assertIsNone(juno.parse_command([juno.NEXT_WORD], {}))

    def test_agent_nickname_keeps_order(self):
        agents = {"vendas": "Vendas · OPS"}
        self.assertEqual(juno.parse_command([juno.AGENT_WORD, "vendas"], agents), f"{juno.AGENT_WORD} vendas")
        self.assertIsNone(juno.parse_command(["vendas", juno.AGENT_WORD], agents))



class RmsTest(unittest.TestCase):
    def test_rms(self):
        self.assertEqual(juno.rms(b""), 0)
        self.assertEqual(juno.rms((16).to_bytes(2, "little", signed=True) * 100), 16)


if __name__ == "__main__":
    unittest.main()
