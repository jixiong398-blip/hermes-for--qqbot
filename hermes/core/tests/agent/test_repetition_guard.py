"""Unit tests for the truncated-response repetition guard."""

from __future__ import annotations

from agent.repetition_guard import MIN_FRAGMENT_LENGTH, is_repetition_dominated


class TestRepetitionGuard:
    def test_repeated_sentence_on_lines_is_detected(self):
        repeated_line = "The model repeated this complete sentence instead of continuing."
        text = ("A short introduction.\n" + repeated_line + "\n") * 80

        assert is_repetition_dominated(text) is True

    def test_repeated_sentence_without_line_breaks_is_detected(self):
        repeated_sentence = "The model repeated this complete sentence instead of continuing. "
        text = repeated_sentence * 200

        assert len(text) >= MIN_FRAGMENT_LENGTH
        assert is_repetition_dominated(text) is True

    def test_long_unique_text_is_not_detected(self):
        text = " ".join(
            f"Sentence {index} describes a distinct topic with token-{index} "
            "and a different conclusion."
            for index in range(400)
        )

        assert len(text) >= MIN_FRAGMENT_LENGTH
        assert is_repetition_dominated(text) is False

    def test_short_repetition_fails_open(self):
        assert is_repetition_dominated("repeat " * 50) is False

    def test_scattered_repetition_is_not_dominant(self):
        filler = " ".join(f"unique filler token {index}" for index in range(1200))
        text = filler + ("\nThe model repeated this complete sentence." * 5)

        assert is_repetition_dominated(text) is False

    def test_non_string_inputs_fail_open(self):
        assert is_repetition_dominated("") is False
        assert is_repetition_dominated(None) is False
        assert is_repetition_dominated(12345) is False
