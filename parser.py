from lark import Lark, Transformer, Token
from pathlib import Path


class Word:
    def __init__(self, text: str):
        self.text = text
        self.ruby: str | None = None
        self.mora: int = self.calc_mora(text)
        self.base_mora = self.mora

    def __str__(self):
        _text = self.text
        if self.ruby is not None:
            _text += f"[{self.ruby}]"
        # if self.mora != len(self.text):
        _text += f".{self.mora}"
        return _text

    @staticmethod
    def calc_mora(text):
        mora = 0
        for c in text:
            if not c.isprintable() or c in (
                "ャ",
                "ュ",
                "ョ",
                "ァ",
                "ィ",
                "ゥ",
                "ェ",
                "ォ",
                "ゃ",
                "ゅ",
                "ょ",
                "ぁ",
                "ぃ",
                "ぅ",
                "ぇ",
                "ぉ",
                " ",
            ):
                continue
            mora += 1
        return mora


class Line:
    def __init__(self, words):
        self.words = words

    def total_mora(self):
        return sum(word.mora for word in self.words)

    def __str__(self):
        return (
            "".join(str(word) for word in self.words) + " | " + str(self.total_mora())
        )


class Chapter:
    def __init__(self, lines):
        self.lines = lines

    def __str__(self):
        return "\n".join(str(line) for line in self.lines)


class Lyrics:
    def __init__(self, chapters):
        self.chapters = chapters

    def __str__(self):
        return "\n----\n".join(str(chapter) for chapter in self.chapters)


class LyricsTransformer(Transformer):
    def line(self, items):
        return Line(items)

    def word(self, items):
        return Word("".join(items))

    def chapter(self, items):
        return Chapter(items)

    def word_ruby(self, items):
        assert len(items) == 2
        word = items[0]
        ruby = items[1]
        assert isinstance(word, Word) and isinstance(ruby, str)
        word.ruby = ruby
        word.mora = word.calc_mora(ruby)
        word.base_mora = word.mora
        return word

    def word_mora(self, items):
        print(items)
        assert len(items) == 2
        word = items[0]
        mora = items[1]
        assert (
            isinstance(word, Word)
            and isinstance(mora, Token)
            and mora.type == "INTEGER"
        )
        word.mora = int(mora.value)
        return word

    def start(self, items):
        return Lyrics(items)


grammar = Path(__file__).parent.joinpath("lyrics.lark").read_text()
parser = Lark(grammar, start="start")
if __name__ == "__main__":
    from timeit import timeit

    example_text = Path("./lyrics.md").read_text()
    grammarv1 = Path(__file__).parent.joinpath("lyrics.lark").read_text()
    grammarv2 = Path(__file__).parent.joinpath("lyricsv2.lark").read_text()
    resv1 = timeit(
        'Lark(grammarv1, start="start").parse(example_text)',
        globals=locals(),
        number=10,
    )
    print(resv1)
    resv2 = timeit(
        'Lark(grammarv2, start="start").parse(example_text)',
        globals=locals(),
        number=10,
    )
    print(resv2)
    # lyrics = LyricsTransformer().transform(res)
    # print(lyrics)
