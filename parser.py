from lark import Lark, Transformer, Token
from pathlib import Path
import re
from rich import print


class Word:
    def __init__(self, text: str, ruby: "Line | None" = None):
        self.text = text
        self.ruby: "Line | None" = None
        self.mora: int = self.calc_mora(text)
        self.base_mora = self.mora  # result of calc_mora，will not be modified by .\d+

        self.is_ruby_mora = False  # whether the mora is calculated from ruby or text
        # True: mora is calculated from ruby
        # False: mora is calculated from text, or overridden by .\d+(only when ruby mora is not overridden)

        if ruby is not None:
            self.set_ruby(ruby)

    def set_ruby(self, ruby: "Line"):
        self.ruby = ruby
        assert all(word.ruby is None for word in ruby.words)
        self.mora = self.ruby.total_mora()
        self.base_mora = self.ruby.total_mora(base=True)

        assert not self.is_ruby_mora
        self.is_ruby_mora = True

    def override_mora(self, mora: int):
        assert self.ruby is None or self.base_mora == self.mora, (
            "Cannot override mora if you have ruby with different mora"
        )
        if self.mora == mora:
            print(f"Warning: override mora to the same value {mora} for word '{self.text}'")
            return
        self.mora = mora
        self.is_ruby_mora = False

    def is_kanji(self):
        return re.match(r"[\u4e00-\u9faf々]+$", self.text)

    def __str__(self):
        _text = self.text
        if self.ruby is not None:
            _text += f"\\[{self.ruby}]"
        # if self.mora != len(self.text):
        if self.mora > 2:
            _text += f".{self.mora}"

        color = ["grey30", "bright_white", "cyan1", "red"][min(self.mora, 3)]

        return f"[{color}]{_text}[/{color}]"

    @staticmethod
    def calc_mora(text):
        mora = 0
        for c in text:
            if (
                not c.isprintable()
                or c in "ャュョァィゥェォゃゅょぁぃぅぇぉ "
                "「」『』、。．・♥※；…‥？！：（）〔〕"
            ):
                continue
            mora += 1
        return mora


class Line:
    def __init__(self, words: list[Word], is_ruby=False):
        self.words: list[Word] = []
        self.is_ruby = is_ruby

        while len(words):
            w = words.pop()
            self.words.insert(0, w)

            if w.ruby is None or not w.is_kanji():
                continue

            while len(words):
                w_next = words[-1]
                if not (w_next.is_kanji() and w_next.ruby is None):
                    break
                words.pop()
                w.text = w_next.text + w.text
                # mora and base_mora is calculated by w.set_ruby

    def total_mora(self, base=False):
        return sum(word.base_mora if base else word.mora for word in self.words)

    def __str__(self):
        return "".join(str(word) for word in self.words) + (
            ""
            if self.is_ruby
            else (" | " + "[green]%s[/green]" % str(self.total_mora()))
        )

    def flatten_ruby(self):
        words_ = []
        for word in self.words:
            if word.ruby is None or word.mora == 1 or not word.is_ruby_mora:
                words_.append(word)
                continue
            print(f"Flattening ruby for word '{word.text}' with ruby '{word.ruby}'")

            for i, w in enumerate(word.ruby.words):
                text = word.text if i == 0 else "#"
                w_ = Word(text)
                w_.set_ruby(Line([w], is_ruby=True))
                words_.append(w_)
        self.words = words_


class Chapter:
    def __init__(self, lines):
        self.lines = lines

    def __str__(self):
        return "\n".join(str(line) for line in self.lines)

    def flatten_ruby(self):
        for line in self.lines:
            line.flatten_ruby()


class Lyrics:
    def __init__(self, chapters):
        self.chapters = chapters

    def __str__(self):
        return "\n----\n".join(str(chapter) for chapter in self.chapters)

    def flatten_ruby(self):
        for chapter in self.chapters:
            chapter.flatten_ruby()


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
        assert isinstance(word, Word) and isinstance(ruby, Line)
        ruby.is_ruby = True
        word.set_ruby(ruby)
        return word

    def word_mora(self, items):
        assert len(items) == 2
        word = items[0]
        mora = items[1]
        assert (
            isinstance(word, Word)
            and isinstance(mora, Token)
            and mora.type == "INTEGER"
        )
        if word.ruby is not None:
            assert word.mora == word.base_mora, (
                "Cannot override mora if you have ruby with overridden mora"
            )
        word.override_mora(int(mora.value))
        return word

    def start(self, items):
        return Lyrics(items)


grammar = Path(__file__).parent.joinpath("lyricsv2.lark").read_text()
parser = Lark(grammar, start="start")
if __name__ == "__main__":
    example_text = Path("./example/lyrics.md").read_text()
    grammarv1 = Path(__file__).parent.joinpath("lyrics.lark").read_text()
    grammarv2 = Path(__file__).parent.joinpath("lyricsv2.lark").read_text()
    res = Lark(grammarv2, start="start").parse(example_text)
    lyrics = LyricsTransformer().transform(res)
    print(str(lyrics))
