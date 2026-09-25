from palmcards.align import align, counts, summary
from palmcards.notes import parse_text

WORD_S = 0.3  # synthetic word length
GAP_S = 0.05


def notes_words(text: str) -> list[list[str]]:
    return [[w.norm for w in s.words] for s in parse_text(text).sentences]


def transcript(text: str, t0: float = 10.0) -> list[dict]:
    """Whisper-style words with made-up times; "|" adds a 1 s silence."""
    words, t = [], t0
    for tok in text.split():
        if tok == "|":
            t += 1.0
            continue
        words.append({"text": " " + tok, "start": round(t, 3), "end": round(t + WORD_S, 3), "probability": 0.9})
        t += WORD_S + GAP_S
    return words


def said(result: dict, words: list[dict], sentence: int) -> list[str | None]:
    return [None if i is None else words[i]["text"].strip() for i in result["sentences"][sentence]["words"]]


NOTES = """\
Good evening, everyone. / Thank you for *being* here tonight.
We know where and we know when. [fall]
[slow] Most of us rehearse in our heads.
"""


def test_exact_reading():
    words = transcript("Good evening, everyone. Thank you for being here tonight. "
                       "We know where and we know when. Most of us rehearse in our heads.")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"]] == ["spoken"] * 4
    assert all(s["coverage"] == 1.0 and s["misheard"] == [] for s in r["sentences"])
    assert r["sentences"][0]["words"] == [0, 1, 2]
    assert r["sentences"][0]["start"] == words[0]["start"]
    assert r["sentences"][0]["end"] == words[2]["end"]
    assert r["sentences"][3]["end"] == words[-1]["end"]
    assert r["fillers"] == r["restarts"] == r["extras"] == r["unsure"] == []


def test_skipped_sentence():
    words = transcript("Good evening everyone. Thank you for being here tonight. | Most of us rehearse in our heads.")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"]] == ["spoken", "spoken", "skipped", "spoken"]
    skipped = r["sentences"][2]
    assert skipped["start"] is None and skipped["end"] is None and skipped["coverage"] == 0.0
    assert skipped["words"] == [None] * 7
    assert r["extras"] == []


def test_skipped_sentences_at_both_ends():
    words = transcript("We know where and we know when.")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"]] == ["skipped", "skipped", "spoken", "skipped"]


def test_ad_lib_inside_and_between_sentences():
    words = transcript("Good evening everyone. Thank you all so very much for being here tonight. "
                       "Honestly it means a lot. We know where and we know when. Most of us rehearse in our heads.")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"]] == ["spoken"] * 4
    ad_libs = [[words[i]["text"].strip() for i in e["words"]] for e in r["extras"]]
    # "so" inside the ad-lib is a filler, and doesn't split it
    assert ad_libs == [["all", "very", "much"], ["Honestly", "it", "means", "a", "lot."]]
    assert [e["sentence"] for e in r["extras"]] == [1, 1]
    assert [f["text"] for f in r["fillers"]] == ["so"]


def test_restart_binds_to_last_attempt():
    words = transcript("Good evening everyone. Thank you for being here tonight. "
                       "We know where... | we know where and we know when. Most of us rehearse in our heads.")
    r = align(notes_words(NOTES), words)
    s = r["sentences"][2]
    assert s["status"] == "spoken" and s["coverage"] == 1.0
    assert s["words"][:3] == [12, 13, 14]  # the second "we know where"
    assert s["start"] == words[12]["start"]
    assert r["restarts"] == [{"sentence": 2, "words": [9, 10, 11]}]
    assert r["extras"] == []


def test_restart_with_cut_off_word():
    words = transcript("We know wh- we know where and we know when.")
    r = align(notes_words(NOTES), words)
    assert r["sentences"][2]["words"] == [3, 4, 5, 6, 7, 8, 9]
    assert r["restarts"] == [{"sentence": 2, "words": [0, 1, 2]}]


def test_several_restarts_in_a_row():
    words = transcript("Thank you for being here tonight. We... we know... | we know where and we know when.")
    r = align(notes_words(NOTES), words)
    assert said(r, words, 2) == ["we", "know", "where", "and", "we", "know", "when."]
    assert r["sentences"][2]["words"][0] == 9
    assert r["restarts"] == [{"sentence": 2, "words": [6, 7, 8]}]


def test_restart_when_the_later_attempt_is_misheard():
    # The first attempt is the better match word for word; the notes still
    # bind to the second one, the one that carried on.
    words = transcript("Thank you for being here tonight. We know where... we know wear and we know when.")
    r = align(notes_words(NOTES), words)
    s = r["sentences"][2]
    assert s["words"][:3] == [9, 10, 11]
    assert s["misheard"] == [2]
    assert r["restarts"] == [{"sentence": 2, "words": [6, 7, 8]}]


def test_misheard_word():
    notes = notes_words("Put it over their heads.")
    words = transcript("Put it over there heads.")
    r = align(notes, words)
    s = r["sentences"][0]
    assert s["status"] == "spoken" and s["coverage"] == 1.0
    assert s["words"] == [0, 1, 2, 3, 4]
    assert s["misheard"] == [3]


def test_unrelated_word_is_not_matched():
    words = transcript("Put it over banana heads.")
    r = align(notes_words("Put it over their heads."), words)
    s = r["sentences"][0]
    assert s["words"] == [0, 1, 2, None, 4]
    assert s["coverage"] == 0.8 and s["status"] == "spoken"
    assert r["extras"] == [{"sentence": 0, "words": [3]}]


def test_fillers():
    words = transcript("Um, good evening everyone. Uh thank you for, like, being here tonight. "
                       "Most of us, um, rehearse in our heads.")
    r = align(notes_words(NOTES), words)
    assert [f["text"] for f in r["fillers"]] == ["um", "uh", "like", "um"]
    assert r["fillers"][0] == {"word": 0, "text": "um", "t": words[0]["start"]}
    assert [s["status"] for s in r["sentences"]] == ["spoken", "spoken", "skipped", "spoken"]
    assert r["extras"] == []


def test_filler_word_in_the_notes_is_a_word():
    words = transcript("It felt like a conversation, so, like, anyway.")
    r = align(notes_words("It felt like a conversation."), words)
    assert r["sentences"][0]["words"] == [0, 1, 2, 3, 4]
    assert [f["word"] for f in r["fillers"]] == [5, 6]
    assert r["extras"] == [{"sentence": 0, "words": [7]}]


def test_partial_sentence():
    words = transcript("Most of us rehearse.")
    r = align(notes_words(NOTES), words)
    s = r["sentences"][3]
    assert s["status"] == "partial" and s["coverage"] == round(4 / 7, 3)
    assert s["end"] == words[3]["end"]


def test_split_and_joined_words():
    words = transcript("Thanks every one, see you some time.")
    r = align(notes_words("Thanks everyone, see you sometime. Sit down."), words)
    s = r["sentences"][0]
    assert s["coverage"] == 1.0 and s["words"] == [0, 1, 3, 4, 5]
    assert s["joined"] == [[1, 2], [4, 6]]
    assert s["end"] == words[6]["end"]
    words = transcript("Sit-down now.")
    r = align(notes_words("Sit down now."), words)
    assert r["sentences"][0]["words"] == [0, 0, 1]


def test_empty_transcript():
    r = align(notes_words(NOTES), [])
    assert [s["status"] for s in r["sentences"]] == ["skipped"] * 4
    assert counts(r) == {"spoken": 0, "partial": 0, "skipped": 4, "fillers": 0, "restarts": 0, "extras": 0}


def test_punctuation_case_and_markup_ignored():
    words = transcript("GOOD EVENING, EVERYONE!! Thank you... for being — here tonight?")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"][:2]] == ["spoken", "spoken"]
    assert r["sentences"][1]["words"] == [3, 4, 5, 6, 8, 9]  # "—" takes no part
    assert r["extras"] == []


def test_chatter_before_and_after():
    words = transcript("OK here goes. Good evening everyone. Thank you for being here tonight. Right, stop.")
    r = align(notes_words(NOTES), words)
    assert [s["status"] for s in r["sentences"]] == ["spoken", "spoken", "skipped", "skipped"]
    assert r["sentences"][0]["words"] == [3, 4, 5]
    assert [e["words"] for e in r["extras"]] == [[0, 1, 2], [12, 13]]


def test_unsure_words_left_out():
    # Whisper's hallucinations in noise come with tiny probabilities.
    words = transcript("Thank you. Most of us rehearse in our heads.")
    words[0]["probability"] = words[1]["probability"] = 0.003
    r = align(notes_words(NOTES), words)
    assert r["unsure"] == [0, 1]
    assert r["sentences"][1]["status"] == "skipped"
    assert r["sentences"][3]["words"] == [2, 3, 4, 5, 6, 7, 8]


def test_summary():
    words = transcript("Um good evening everyone. We know where we know where and we know when.")
    r = align(notes_words(NOTES), words)
    assert summary(r) == "2/4 sentences spoken, 2 skipped, 1 filler, 1 restart"
