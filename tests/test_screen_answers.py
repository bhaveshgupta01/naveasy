"""Questions about what is on screen are answered from the on-screen facts, or not at all."""
from backend.app import screen

SCREEN = {"list": {"count": 2971, "top": [{"name": "The Rice", "score": 84}, {"name": "Calais Midtown", "score": 84}]},
          "hex": {"score": 70, "live": True, "text": ["Pharmacy 10 min", "Bus stop 2 min"]}}


def test_answers_come_from_what_is_shown():
    a = screen.answer("How many homes are listed?", SCREEN, ["wheelchair"])
    assert a and "2,971 homes" in a["text"] and "The Rice" in a["text"]
    b = screen.answer("what does the green color mean on the map", SCREEN, ["wheelchair"])
    assert b and "green homes score 75 or more" in b["text"]
    c = screen.answer("where does the data come from?", SCREEN, ["wheelchair"])
    assert c and "Matterport" in c["text"] and "Vexcel" in c["text"]


def test_unrelated_questions_get_no_answer():
    assert screen.answer("What is the capital of France?", SCREEN, ["wheelchair"]) is None
    assert screen.answer("", SCREEN, ["wheelchair"]) is None


def test_word_matching():
    assert screen._tokens("How wide are the doorways?") == ["wide", "doorway"]
    assert "rent" in screen._expand(["expensive"]) and "door" in screen._expand(["doorway"])


def test_counter_height_rule_is_explained():
    a = screen.answer("How high should a kitchen counter be for a wheelchair?", SCREEN, ["wheelchair"])
    assert a and "34 inches or lower" in a["text"] and "ADA" in a["text"]
