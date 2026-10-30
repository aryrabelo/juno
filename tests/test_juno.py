"""Testes das partes puras do juno.py (sem microfone e sem apertar teclas).

Rodar: .venv/bin/python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import time
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
    shortcuts = {"salvar": ("keys", "command+s"), "agente próximo": ("openbot", "next")}

    def test_shortcut_only_on_its_own(self):
        self.assertEqual(juno.parse_command(["salvar"], self.shortcuts), "salvar")
        self.assertEqual(juno.parse_command(["agente", "próximo"], self.shortcuts), "agente próximo")
        self.assertIsNone(juno.parse_command(["[unk]", "salvar"], self.shortcuts))
        self.assertIsNone(juno.parse_command(["próximo", "agente"], self.shortcuts))

    def test_send_word_at_the_end_wins(self):
        send = " ".join(juno.SEND_PHRASE)
        self.assertEqual(juno.parse_command(["salvar", *juno.SEND_PHRASE], self.shortcuts), send)


class CanSendTest(unittest.TestCase):
    def test_only_with_dictation_on_this_mac(self):
        self.assertTrue(juno.can_send(recording=True, idle_s=None))
        self.assertTrue(juno.can_send(recording=False, idle_s=5))  # parou o ditado na mão e disse câmbio
        self.assertTrue(juno.can_send(recording=False, idle_s=juno.DICTATION_RECENT_S))
        self.assertFalse(juno.can_send(recording=False, idle_s=juno.DICTATION_RECENT_S + 1))
        self.assertFalse(juno.can_send(recording=False, idle_s=None))  # nenhum ditado no log


class FluidStateTest(unittest.TestCase):
    def state(self, *lines):
        old = juno.FLUID_LOG
        with tempfile.TemporaryDirectory() as tmp:
            juno.FLUID_LOG = Path(tmp) / "Fluid.log"
            juno.FLUID_LOG.write_text("".join(f"[{time.strftime('%H:%M:%S', time.localtime(time.time() - ago))}.000]"
                                              f" [INFO] [ASRService] {msg}\n" for ago, msg in lines))
            try:
                return juno.fluid_state()
            finally:
                juno.FLUID_LOG = old

    def test_recording(self):
        self.assertEqual(self.state((30, "STOP() called"), (5, "START() called"))[0], True)

    def test_seconds_since_last_dictation(self):
        recording, idle = self.state((20, "START() called"), (8, "STOP() called"))
        self.assertFalse(recording)
        self.assertIn(idle, (8, 9))

    def test_no_log(self):
        self.assertEqual(self.state(), (False, None))


class CleanShortcutsTest(unittest.TestCase):
    def test_valid_actions_and_phrase_normalized(self):
        raw = {"Salvar": {"keys": "command+s"}, "abre  navegador": {"open": "Safari"},
               "backup": {"run": "~/bin/backup.sh"}, "agente vendas": {"openbot": "Vendas · OPS"}}
        self.assertEqual(juno.clean_shortcuts(raw), {
            "salvar": ("keys", "command+s"), "abre navegador": ("open", "Safari"),
            "backup": ("run", "~/bin/backup.sh"), "agente vendas": ("openbot", "Vendas · OPS")})

    def test_invalid_actions_are_dropped(self):
        raw = {"a": {"teclas": "command+s"}, "b": {"keys": "command+s", "open": "Safari"},
               "c": "command+s", "d": {"open": "  "}, "e": {"keys": "hyper+s"}}
        self.assertEqual(juno.clean_shortcuts(raw), {})

    def test_phrases_that_could_never_fire_are_dropped(self):
        raw = {juno.STOP_WORD: {"keys": "command+s"},
               f"abre {juno.WAKE_WORD}": {"open": "Safari"},
               " ".join(["salvar", *juno.SEND_PHRASE]): {"keys": "command+s"}}
        self.assertEqual(juno.clean_shortcuts(raw), {})


class MigrateConfigTest(unittest.TestCase):
    def test_old_openbot_keys_become_shortcuts(self):
        old = {"language": "pt", "wake_word": "lila", "agent_word": "agente", "next_word": "próximo",
               "app": "OpenBot", "agents": {"vendas": "Vendas · OPS"}}
        config, changed = juno.migrate_config(old)
        self.assertTrue(changed)
        self.assertEqual(config, {"language": "pt", "wake_word": "lila", "shortcuts": {
            "agente próximo": {"openbot": "next"}, "próximo agente": {"openbot": "next"},
            "agente vendas": {"openbot": "Vendas · OPS"}}})

    def test_language_default_words_and_existing_shortcut_kept(self):
        old = {"language": "en", "agents": {"sales": "Sales"}, "shortcuts": {"agent sales": {"open": "Mail"}}}
        config, _ = juno.migrate_config(old)
        self.assertEqual(config["shortcuts"], {"agent sales": {"open": "Mail"}, "agent next": {"openbot": "next"},
                                               "next agent": {"openbot": "next"}})

    def test_legacy_tsv_merged_config_wins(self):
        config, _ = juno.migrate_config({"agents": {"vendas": "Novo"}}, {"vendas": "Velho", "notas": "Notas"})
        self.assertEqual(config["shortcuts"]["agente vendas"], {"openbot": "Novo"})
        self.assertEqual(config["shortcuts"]["agente notas"], {"openbot": "Notas"})

    def test_new_format_untouched(self):
        new = {"language": "pt", "shortcuts": {"salvar": {"keys": "command+s"}}}
        self.assertEqual(juno.migrate_config(new), (new, False))



class RmsTest(unittest.TestCase):
    def test_rms(self):
        self.assertEqual(juno.rms(b""), 0)
        self.assertEqual(juno.rms((16).to_bytes(2, "little", signed=True) * 100), 16)


if __name__ == "__main__":
    unittest.main()
