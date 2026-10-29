"""例句质量的共用规则 —— build_wordlist.py 与 gen_examples.py 必须用同一套。

为什么单独抽出来：
  两个脚本分开跑（一个生成、一个组装），如果各写一份判定，很容易出现
  「生成时认为这句话不合格所以补了一条，组装时又认为它合格所以用了旧的」，
  最后词库里既有短语冒充的例句，又有白花钱生成的例句。
"""

from __future__ import annotations

import re

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def is_sentence(text: str) -> bool:
    """上游这条例句够不够当「例句」。

    判据只有一条：**原文以 . ! ? 结尾**。

    ★ 为什么是标点而不是词数 / 有没有谓语：
      上游 exam-data 的例句是从试卷原文里截的，其中三分之一是
      「lose faith」「a bedroom carpet」「rock the world」这种片段，
      直接加个句号当例句显示就是骗人 —— 卡面写着「例句」，用户读到的不是句子。

      试过三种判据，只有标点这条不误伤：
        · 词数 < 5 当短语   → 砍掉 "You scared me."（完整句）
        · 首词是冠词当短语 → 砍掉 "The results support this hypothesis strongly."
        · 找助动词/动词表   → 砍掉 "She gave me a maternal smile."（gave 没进表）
      而标点这条，实测 1799 条无标点的全是片段、3479 条有标点的全是完整句，
      边界干净得像是上游替我们标好的。

      因此生成那边也硬性要求句末有标点，两边判据同源，不会互相打架。
    """
    s = (text or "").strip()
    return bool(s) and s[-1] in ".!?"


def _stem(word: str) -> str:
    """粗词干：长词去掉最后两个字母，短词去掉一个，用来容忍 rose / teeth 这类变形。"""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if len(w) <= 3:
        return w
    if len(w) <= 5:
        return w[:-1]
    return w[:-2]


# 不规则变形。只用于「这句里到底有没有出现目标词」的判定 ——
# 不加这张表，模型写 "The wind blew hard." 会被判成「句子里没有 blow」而白丢一条。
# 表小不要紧：漏掉的只是少数词，代价是多生成一次；放宽到「前两字母相同」会让
# black / block 这种真不同的词互相顶替，那才是真错误。
_IRREGULAR = {
    "be": {"am", "is", "are", "was", "were", "been", "being"},
    "bear": {"bore", "borne", "born"},
    "beat": {"beaten"},
    "become": {"became"},
    "begin": {"began", "begun"},
    "blow": {"blew", "blown"},
    "break": {"broke", "broken"},
    "bring": {"brought"},
    "buy": {"bought"},
    "catch": {"caught"},
    "choose": {"chose", "chosen"},
    "come": {"came"},
    "deal": {"dealt"},
    "do": {"did", "done", "does"},
    "draw": {"drew", "drawn"},
    "drink": {"drank", "drunk"},
    "drive": {"drove", "driven"},
    "eat": {"ate", "eaten"},
    "fall": {"fell", "fallen"},
    "feel": {"felt"},
    "fight": {"fought"},
    "find": {"found"},
    "fly": {"flew", "flown"},
    "fling": {"flung"},
    "forget": {"forgot", "forgotten"},
    "freeze": {"froze", "frozen"},
    "get": {"got", "gotten"},
    "give": {"gave", "given"},
    "go": {"went", "gone"},
    "grow": {"grew", "grown"},
    "hang": {"hung"},
    "have": {"has", "had"},
    "hear": {"heard"},
    "hide": {"hid", "hidden"},
    "hold": {"held"},
    "keep": {"kept"},
    "know": {"knew", "known"},
    "lead": {"led"},
    "leave": {"left"},
    "lend": {"lent"},
    "lie": {"lay", "lain"},
    "lose": {"lost"},
    "make": {"made"},
    "mean": {"meant"},
    "meet": {"met"},
    "pay": {"paid"},
    "ride": {"rode", "ridden"},
    "ring": {"rang", "rung"},
    "rise": {"rose", "risen"},
    "run": {"ran"},
    "say": {"said"},
    "see": {"saw", "seen"},
    "seek": {"sought"},
    "sell": {"sold"},
    "send": {"sent"},
    "shake": {"shook", "shaken"},
    "shine": {"shone"},
    "shoot": {"shot"},
    "show": {"shown"},
    "sing": {"sang", "sung"},
    "sink": {"sank", "sunk"},
    "sit": {"sat"},
    "sleep": {"slept"},
    "speak": {"spoke", "spoken"},
    "spend": {"spent"},
    "spit": {"spat"},
    "stand": {"stood"},
    "steal": {"stole", "stolen"},
    "strike": {"struck"},
    "swim": {"swam", "swum"},
    "take": {"took", "taken"},
    "teach": {"taught"},
    "tear": {"tore", "torn"},
    "tell": {"told"},
    "think": {"thought"},
    "throw": {"threw", "thrown"},
    "tooth": {"teeth"},
    "foot": {"feet"},
    "understand": {"understood"},
    "wake": {"woke", "woken"},
    "wear": {"wore", "worn"},
    "win": {"won"},
    "write": {"wrote", "written"},
}


def mentions_word(word: str, sentence: str) -> bool:
    """这句话里到底有没有出现目标词（容忍常见变形）。"""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    got = re.sub(r"[^a-z]", " ", (sentence or "").lower())
    if not w:
        return False
    if _stem(w) and _stem(w) in re.sub(r"[^a-z]", "", (sentence or "").lower()):
        return True
    flat = " " + re.sub(r"[^a-z]", " ", (sentence or "").lower()) + " "
    forms = {w}
    for k, vs in _IRREGULAR.items():
        if w == k:
            forms |= vs
        elif w in vs:
            forms.add(k)
            forms |= vs
    return any(f" {f} " in flat or f" {f}s " in flat or f" {f}d " in flat or f" {f}ed " in flat
               for f in forms if f)


def check_generated(word: str, sentence: str, translation: str, min_words: int = 5) -> str:
    """我们自己生成的例句要过的关卡。返回空串表示通过。"""
    s = (sentence or "").strip()
    z = (translation or "").strip()
    if not s or not z:
        return "空"
    if len(s) < 12:
        return "太短"
    if len(s) > 160:
        return "太长"
    if CJK_RE.search(s):
        return "例句含中文"
    if len(CJK_RE.findall(z)) < 2:
        return "译文不像中文"
    if not WORD_RE.search(s):
        return "没有英文"
    if count_words(s) < min_words:
        return "词太少，不像完整句"
    if not mentions_word(word, s):
        return "句子里没有这个词"
    if not s[0].isupper():
        return "首字母没大写"
    if not is_sentence(s):
        return "没有句末标点"
    return ""


def check_bank(word: str, sentence: str, translation: str) -> str:
    """词库成品要过的关卡 —— 与 check_generated 同源，只放开「词太少」。

    为什么不直接用 check_generated 卡词库：
      那条 5 词下限是给**模型产出**设的（模型很容易只写半句就交差）。
      上游真题里有一批短而完整的句子 —— "It doesn't matter."、
      "You scared me."、"God bless you." —— 对背单词的人来说短句反而更好记，
      按模型的尺子卡掉它们是错的。实测这类只有 26 条（6661 词里 0.4%）。
    """
    return check_generated(word, sentence, translation, min_words=1)
