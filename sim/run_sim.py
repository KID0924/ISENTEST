# -*- coding: utf-8 -*-
"""
《天堂倖存者》70 NPC 生態與經濟本機模擬器（未來可直接由 GitHub Actions 排程執行）

特性：
1. 直接解析 2_輕量版 內的遊戲真實數值：
   - plevel.js (EXPT / MEXP / BXP / REC / MAXLV)
   - data.js (DATA.monsters 掉寶與等級)
   - items.js / items2.js / items3.js / itemuse.js (ITEMS / GEARCATS / ITEMUSE / STARTW)
   - game.js (STAGES / ENCH / enchRate / SHOPSELL / BUYP / VNPC 商店價格)
2. 固定維護 70 位模擬 NPC（7 職業各 10 位），依真實生態比例分 4 類：
   - whale (5): 重課肝帝 35~50 場/天
   - regular (35): 一般冒險者 12~25 場/天
   - casual (20): 新手探險家 8~15 場/天（急售便宜貨 0.65~0.85×）
   - scalper (10): 奇岩商人 12~25 場/天（低掃 <=0.80×、加價轉賣 1.04~1.16×，金幣上限 2 億）
3. 具備完整防通膨與資源回收機制：
   - 依真實 enchRate() 衝裝（失敗爆裝消耗裝備與卷軸）
   - 藥水/傳捲消耗、拍賣交易 10% 手續費銷毀
   - Lv.50 畢業老玩家退坑＋同職階 Lv.1 新手補充
   - 動態市場基準價 marketPrice（供需調整 + 全服通膨阻尼）
4. 離線範本台詞與「依正在販賣的商品叫賣」語句生成（亦預留選配 --ai 接口）。
5. 產出 world_state.json 並同步寫入 2_輕量版 與 1_完整版 根目錄。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

CLASSES = ["royal", "knight", "elf", "mage", "darkelf", "dk", "ill"]
CLS_ZH = {
    "royal": "王族",
    "knight": "騎士",
    "elf": "妖精",
    "mage": "法師",
    "darkelf": "黑暗妖精",
    "dk": "龍騎士",
    "ill": "幻術師",
}
TIER_COUNTS = {"whale": 5, "regular": 35, "casual": 20, "scalper": 10}
WHALE_RETIRE_QUOTAS = {"scalper": 2, "regular": 4, "casual": 3}
TIER_ZH = {
    "whale": "重課肝帝",
    "regular": "一般冒險者",
    "casual": "新手探險家",
    "scalper": "奇岩商人",
}
TIER_RUNS_PER_DAY = {
    "whale": (35, 50),
    "regular": (12, 25),
    "casual": (8, 15),
    "scalper": (12, 25),
}
TIER_SELL_MULT = {
    "whale": (1.00, 1.10),
    "regular": (0.95, 1.05),
    "casual": (0.65, 0.85),
    "scalper": (1.04, 1.16),
}
TIER_BUY_TOL = {
    "whale": 1.25,
    "regular": 1.12,
    "casual": 0.90,
    "scalper": 0.80,
}
TIER_GOLD_CAP = {
    "scalper": 200_000_000,
    "whale": 54_000_000,
    "regular": 22_000_000,
    "casual": 7_500_000,
}
TIER_SINK_CAP = {
    "scalper": 160_000_000,
    "whale": 44_000_000,
    "regular": 18_000_000,
    "casual": 6_000_000,
}

EVENT_CATS = ("weapon", "armor", "gem", "scroll", "spellbook", "material", "potion", "etc")

# 20 張突發每日事件卡（主軸區間 +15% ~ -45%，含 4 張深跌、4 張滿多、6 張品類輪動、6 張規則／生態事件）
DAILY_EVENT_POOL: list[dict[str, Any]] = [
    # --- 4 張大空頭暴跌 (-28% ~ -45%) ---
    {"id": "crash_panic", "title": "📉【股災恐慌拋售潮】", "desc": "黑市傳出崩盤謠言，全服冒險者恐慌殺低變現！", "mults": {"all": 0.55}, "sellUrgency": 0.88},
    {"id": "blackmarket_raid", "title": "🚨【警衛隊查緝走私】", "desc": "奇岩衛兵臨檢黑市倉庫，武器防具與材料大跳水！", "mults": {"weapon": 0.60, "armor": 0.60, "material": 0.66, "scroll": 0.85}, "sellUrgency": 0.90},
    {"id": "dungeon_flood", "title": "🌊【地監產能大過剩】", "desc": "遠征隊大豐收導致武器防具供過於求，裝備行情重挫！", "mults": {"weapon": 0.65, "armor": 0.65, "gem": 0.90}, "sellUrgency": 0.92},
    {"id": "chill_selloff", "title": "❄️【冷清縮手拋售日】", "desc": "市場買氣急凍，各類物資全面折價出清！", "mults": {"all": 0.72}, "sellUrgency": 0.90},
    # --- 4 張多頭採購 (+10% ~ +15%) ---
    {"id": "aden_bull", "title": "📈【亞丁繁榮上漲日】", "desc": "奇岩經濟活絡買氣穩健走強，全品項看漲！", "mults": {"all": 1.15}},
    {"id": "siege_supply", "title": "🔥【皇室攻城大採購】", "desc": "王城備戰急徵武防捲軸與藥水，消耗補給走強！", "mults": {"scroll": 1.15, "potion": 1.15, "armor": 1.10}},
    {"id": "dragon_carnival", "title": "🐉【屠龍慶典備戰】", "desc": "屠龍風潮帶動戰備需求，武器防具與寶石溫和上揚！", "mults": {"weapon": 1.13, "armor": 1.12, "gem": 1.15}},
    {"id": "guild_hype", "title": "📚【商會稀有囤貨潮】", "desc": "大公會收購稀有材料與古法書，稀缺品項走高！", "mults": {"material": 1.14, "spellbook": 1.15, "potion": 0.85}},
    # --- 6 張品類分化（漲幅至 +15%、跌幅至 -35%） ---
    {"id": "gem_boom", "title": "💎【奇岩寶石拍賣盛會】", "desc": "宮廷工匠高價徵收寶石，白裝卻大幅跌價乏人問津！", "mults": {"gem": 1.15, "material": 1.08, "weapon": 0.72, "armor": 0.75}},
    {"id": "smith_week", "title": "⚒️【鐵匠衝裝競賽週】", "desc": "武防捲軸搶破頭，原料價格壓低讓利！", "mults": {"scroll": 1.15, "material": 0.72, "armor": 1.08}},
    {"id": "novice_relief", "title": "🧪【新手補給救濟日】", "desc": "藥水略漲但低中階武器防具大降價出清！", "mults": {"potion": 1.14, "weapon": 0.65, "armor": 0.65}},
    {"id": "mage_symposium", "title": "🔮【象牙塔法師研討會】", "desc": "魔法書與藍寶石系看漲，近戰武器走低！", "mults": {"spellbook": 1.15, "gem": 1.12, "weapon": 0.74}},
    {"id": "armor_repair", "title": "🛡️【前線防具修補潮】", "desc": "防具與防捲喊漲，進攻型武器大幅回檔！", "mults": {"armor": 1.15, "scroll": 1.10, "weapon": 0.74}},
    {"id": "alchemy_boom", "title": "⚗️【鍊金工坊搶料大戰】", "desc": "製作材料走俏，成品裝反而遇冷跌價！", "mults": {"material": 1.15, "weapon": 0.72, "armor": 0.72}},
    # --- 6 張特殊規則／生態事件 ---
    {"id": "berserk_enchant", "title": "🔥【狂戰衝裝祭典】", "desc": "今日全服衝裝上限 +1，捲軸與高強化裝搶手！", "mults": {"scroll": 1.15, "weapon": 1.08, "armor": 1.08}, "enchantBonus": 1},
    {"id": "merchant_monopoly", "title": "💰【大盤商聯合掃貨週】", "desc": "奇岩商人提高收購線至 0.88×，市場流動加速！", "mults": {"all": 1.04}, "scalperBuyMax": 0.88},
    {"id": "tax_holiday", "title": "⚖️【奇岩商會減稅日】", "desc": "今日交易稅由 10% 降至 3%，全服買賣更活絡！", "mults": {"all": 1.02}, "taxRate": 0.03},
    {"id": "nightmare_raid", "title": "☠️【魔王軍團突襲警報】", "desc": "高手勇闖高難路線，藥水微漲但材料因寶物湧入大跌！", "mults": {"potion": 1.14, "material": 0.72, "gem": 1.08}, "forceHard": True},
    {"id": "chest_harvest", "title": "🎁【寶箱開採大豐收】", "desc": "大批寶箱材料與寶石出爐，素材與寶石折價出清！", "mults": {"material": 0.68, "gem": 0.70, "scroll": 0.82}},
    {"id": "steady_calm", "title": "🌤️【奇岩休整微震日】", "desc": "市場回歸平穩微震，主要反映現實台美股與台北氣候。", "mults": {"all": 0.96}},
]

_LIVE_SIGNAL_CACHE: dict[str, Any] | None = None

# 各職業預設基底屬性（僅供隊友招募面板顯示與戰力估算）
CLASS_BASE_STATS = {
    "royal": {"str": 16, "dex": 11, "con": 12, "wis": 12, "cha": 18, "int": 11},
    "knight": {"str": 18, "dex": 14, "con": 16, "wis": 10, "cha": 10, "int": 10},
    "elf": {"str": 11, "dex": 18, "con": 13, "wis": 13, "cha": 9, "int": 12},
    "mage": {"str": 8, "dex": 8, "con": 15, "wis": 17, "cha": 8, "int": 18},
    "darkelf": {"str": 17, "dex": 17, "con": 11, "wis": 11, "cha": 9, "int": 11},
    "dk": {"str": 17, "dex": 12, "con": 15, "wis": 11, "cha": 9, "int": 12},
    "ill": {"str": 11, "dex": 11, "con": 13, "wis": 15, "cha": 10, "int": 17},
}

CLASS_WEAPONS = {
    "royal": {"sword", "dagger", "tohandsword", "spear", "blunt", "staff"},
    "knight": {"sword", "dagger", "tohandsword", "spear", "blunt", "tohandblunt"},
    "elf": {"bow"},  # 遊戲規則：精靈僅用弓
    "mage": {"staff", "dagger", "sword"},
    "darkelf": {"claw", "edoryu", "dagger", "sword"},
    "dk": {"tohandsword", "sword", "blunt", "tohandblunt", "spear"},
    "ill": {"staff", "blunt", "tohandblunt"},
}
TWO_HAND_WEAPONS = {"tohandsword", "tohandblunt", "bow", "spear", "claw", "edoryu"}

# 圖鑑變身卡階級（對應 game.js FT / FD / PFAB 稀有度與對群清怪覆蓋率）
CARD_ORDER = ["white", "green", "blue", "red", "purple", "gold"]
CARD_TIERS: dict[str, dict[str, Any]] = {
    "white":  {"rank": 0, "zh": "白卡", "dmg": 1.05, "aspd": 1.00, "aoe": 1.15, "hp": 25,  "ac": 0,  "names": ["哥布林", "骷髏", "狼人", "妖魔鬥士", "骷髏弓箭手"]},
    "green":  {"rank": 1, "zh": "綠卡", "dmg": 1.10, "aspd": 1.05, "aoe": 1.55, "hp": 35,  "ac": -3, "names": ["史巴托", "萊肯", "長者", "黑騎士", "食人妖精", "高侖石頭怪"]},
    "blue":   {"rank": 2, "zh": "藍卡", "dmg": 1.18, "aspd": 1.12, "aoe": 2.25, "hp": 55,  "ac": -7, "names": ["食人妖精王", "精英黑騎士", "歐吉"]},
    "red":    {"rank": 3, "zh": "紅卡", "dmg": 1.32, "aspd": 1.16, "aoe": 3.25, "hp": 65,  "ac": -8, "names": ["紅卡英雄戰士", "紅卡神射手", "紅卡魔導士"]},
    "purple": {"rank": 4, "zh": "紫卡", "dmg": 1.50, "aspd": 1.22, "aoe": 4.30, "hp": 110, "ac": -10, "names": ["傳說戰神", "傳說追風", "傳說大魔導"]},
    "gold":   {"rank": 5, "zh": "金卡", "dmg": 1.82, "aspd": 1.28, "aoe": 5.50, "hp": 160, "ac": -15, "names": ["神話君王", "神話精靈", "神話法皇", "神話龍騎"]},
}
# 武器對群係數（對應 levelup.js 穿透／擴散／範圍魔法設計）
WEAPON_AOE: dict[str, float] = {
    "staff": 1.75, "tohandstaff": 1.85, "kiringku": 1.80,
    "bow": 1.55, "spear": 1.50, "chainsword": 1.50,
    "tohandblunt": 1.40, "gauntlet": 1.35, "edoryu": 1.25, "tohandsword": 1.22,
    "sword": 1.08, "dagger": 1.08, "singlespear": 1.15, "claw": 1.10, "blunt": 1.18,
}

# 原版 enchRate 常數（見 game.js L997-998）
ENCH_BASE = 0.50
ENCH_STEP = 0.06
ENCH_MIN = 0.12
ENCH_HARD = [0.20, 0.10, 0.05, 0.03, 0.01]
ENCH_HARD_W = [0.20, 0.05]
ENCH_MAX = 9

SCROLL_ARMOR_NORMAL = 40074
SCROLL_WEAPON_NORMAL = 40087
SCROLL_ARMOR_BLESSED = 140074
SCROLL_WEAPON_BLESSED = 140087
SCROLL_ARMOR_PERFECT = 90074
SCROLL_WEAPON_PERFECT = 90087
SCROLL_GOLD_COIN = 49100
GOLD_ID = 40308

NO_DROP = {41246, 40056, 40005, 40126, 40119, 40319}
NO_SELL = {172, 40743, 40319, 40308, 49100}
MATK = {
    40513: 1 / 3,
    40499: 6.75,
    40519: 12.0,
    40507: 12.0,
    40506: 9.0,
    88: 7.5,
    40515: 7.5,
    40496: 6.75,
    40494: 2.7,
}
CHEST_MATS = [40496, 40519, 40515, 40499, 40507, 40045, 40049, 88, 40048, 40509]

AFFIX_POOL = [
    {"k": "atk", "name": "勇猛", "desc": "傷害 +3%"},
    {"k": "hit", "name": "精準", "desc": "命中 +2"},
    {"k": "hp", "name": "體魄", "desc": "HP +35"},
    {"k": "dr", "name": "守護", "desc": "減傷 +1"},
    {"k": "spd", "name": "迅捷", "desc": "攻速 +3%"},
    {"k": "crit", "name": "必殺", "desc": "暴擊 +2%"},
]

SCALPER_NAMES = [
    "奇岩大盤商", "老張收武捲", "阿明雜貨鋪", "祝福捲大亨", "專賣過安定",
    "物價風向球", "低買高賣王", "小本生意仔", "紅水批發站", "奇岩擺攤王",
    "誠信交易所", "老K百貨鋪", "貨源充足", "便宜沒好貨", "撿漏達人",
]

NPC_NAME_POOL = [
    # 王族/騎士風
    "紫禁之巔", "亞丁盟主", "肯特領主", "傲慢戰神", "血染征途", "蒼穹劍聖", "鐵血巴拉", "奇岩城主", "星塵騎士", "銀月破曉",
    # 妖精/遊俠風
    "風之精靈", "森野追風", "月影神射", "艾爾莎弓手", "WindElf", "綠林旅人", "精靈餅乾控", "霜翼箭雨", "微風輕語", "孤鷹射手",
    # 法師/幻術風
    "冰錐亂飛", "魔力溢位", "亞丁大魔導", "巴風特之眼", "烈焰風暴", "Qn的小徒弟", "星辰賢者", "幻夢立方", "魔結晶收藏", "深淵詠唱者",
    # 黑妖/龍騎風
    "暗影雙刃", "幽冥利爪", "龍之血脈", "暗殺小隊長", "黑暗魅影", "熾炎龍騎", "黑夜疾風", "深紅屠龍者", "影子舞者", "倫德雙刀控",
    # 真實台服生態 ID
    "無課也想飛", "阿龍打寶去", "Momo茶", "水蛇腰", "肝帝不睡覺", "只打不強化", "爆裝哭哭喔", "今天會掉寶", "加班掛機中",
    "Ken仔", "專業撿骨師", "說話島農夫", "Antares", "Koma77", "LinTaiwan", "BaphoHunter", "GiranBoss",
    "半夜不睡覺", "小資玩家", "裝備衝爆王", "紅水喝不完", "歐吉恐懼症", "菜鳥求帶飛", "地監探險王", "純白萬能藥",
    "風木守衛", "海音漁夫", "龍谷礦工", "螞蟻窩常客", "試煉之路", "老玩家的浪漫", "奇岩守門員", "不死鳥", "暴走精靈", "大叔打電動",
]


def ench_rate(kind: str, ench: int, safe: int, blessed: bool = False, uncapped: bool = False) -> float:
    """復刻 game.js:enchRate 的真實成功率公式（uncapped=True 時不封頂於 +9）"""
    if safe < 0:
        return 0.0
    ch = max(ENCH_MIN, ENCH_BASE - ENCH_STEP * (ench - safe))
    table = ENCH_HARD_W if kind == "weapon" else ENCH_HARD
    hs = 7 if kind == "weapon" else safe + 1
    if ench >= hs:
        idx = min(len(table) - 1, ench - hs)
        ch = min(ch, table[idx])
    if blessed:
        ch = min(1.0, ch * 1.5)
    if not uncapped and ench >= ENCH_MAX:
        ch = 0.003 if blessed else 0.0
    return max(0.0, min(1.0, ch))


def _npc_enchant_step_prob(
    kind: str, cur_e: int, safe: int, blessed: bool = False, uncapped: bool = False
) -> float:
    """NPC 強化成功率：安定前必過；超過安定值 +2 以內減半；超過安定值 +2 恢復正常機率"""
    if cur_e < safe:
        return 1.0
    p = ench_rate(kind, cur_e, safe, blessed=blessed, uncapped=uncapped)
    if 0 <= safe <= cur_e < safe + 2:
        return p / 2.0
    return p


def _extract_js_object(text: str, marker: str) -> Any:
    """從 JS const X = {...}; 或 Object.assign(ITEMS, {...}); 中安全擷取 JSON 結構"""
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"Marker not found: {marker}")
    pos_obj = text.find("{", idx)
    pos_arr = text.find("[", idx)
    cands = [p for p in (pos_obj, pos_arr) if p >= 0]
    if not cands:
        raise ValueError(f"No JSON start after marker: {marker}")
    start = min(cands)
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ValueError(f"Unclosed JSON block for marker: {marker}")


class GameDatabase:
    """從 2_輕量版 解析所有遊戲基礎數值"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.items: dict[int, dict[str, Any]] = {}
        self.itemuse: dict[int, str] = {}
        self.startw: dict[str, int] = {}
        self.gear_prices: dict[int, int] = {}
        self.monsters: list[dict[str, Any]] = []
        self.monsters_by_id: dict[int, dict[str, Any]] = {}
        self.expt: list[int] = []
        self.mexp: dict[int, int] = {}
        self.bxp: dict[int, int] = {}
        self.rec: dict[int, int] = {}
        self.stages: list[dict[str, Any]] = []
        self.segments: list[dict[str, Any]] = []
        self.diffs: list[dict[str, Any]] = []
        self.stage_mobs: set[int] = set()
        self.recipes: list[dict[str, Any]] = []
        self.recipes_by_out: dict[int, list[dict[str, Any]]] = {}
        self.item_fx: dict[int, dict[str, float]] = {}
        self.no_drop: set[int] = set(NO_DROP)
        self.no_sell: set[int] = set(NO_SELL)
        self.matk: dict[int, float] = dict(MATK)
        self.chest_mats: list[int] = list(CHEST_MATS)
        self.box_pool: list[int] = []
        self.shop_anchor: dict[int, int] = {}
        self.market_base: dict[int, int] = {}
        self.mcards_cat: list[dict[str, Any]] = []
        self.mcard_by_id: dict[int, dict[str, Any]] = {}
        self.mcard_fx: dict[str, tuple[str, list[int]]] = {
            "fire": ("dmgpct", [1, 2, 5]),
            "water": ("shield", [6, 12, 30]),
            "wind": ("hit", [1, 2, 5]),
            "earth": ("hp", [10, 20, 50]),
            "none": ("exppct", [2, 4, 10]),
        }
        self.msock_slots: dict[str, int] = {
            "weapon": 3, "helm": 1, "armor": 1, "pants": 1,
            "boots": 1, "glove": 1, "shield": 1, "cloak": 1,
        }
        self.mcard_drop_mob: float = 0.02
        self.mcard_drop_boss: float = 0.15
        self.mcard_fuse_rate: dict[int, float] = {1: 0.55, 2: 0.30}
        self.mcard_unsock: dict[int, tuple[int, int]] = {
            1: (100_000, 5), 2: (500_000, 20), 3: (2_000_000, 50),
        }

        self._load()

    def _read(self, name: str) -> str:
        return (self.data_dir / name).read_text(encoding="utf-8")

    def _load(self) -> None:
        # 1. items.js / items2.js / items3.js
        for fn, marker in [
            ("items.js", "const ITEMS="),
            ("items2.js", "Object.assign(ITEMS,"),
            ("items3.js", "Object.assign(ITEMS,"),
        ]:
            obj = _extract_js_object(self._read(fn), marker)
            for k, v in obj.items():
                iid = int(k)
                self.items[iid] = v

        # 移除地圖傳送捲或活動/測試專用不可交易品
        gear_text = self._read("items3.js")
        gearcats = _extract_js_object(gear_text, "const GEARCATS=")
        for cat in gearcats:
            for entry in cat.get("ids", []):
                iid, pr = int(entry[0]), int(entry[1])
                if pr > 1:
                    self.gear_prices[iid] = max(self.gear_prices.get(iid, 0), pr * 10)

        # 2. itemuse.js
        iu_txt = self._read("itemuse.js")
        for k, v in _extract_js_object(iu_txt, "const ITEMUSE=").items():
            self.itemuse[int(k)] = str(v)
        for k, v in _extract_js_object(iu_txt, "const STARTW=").items():
            self.startw[str(k)] = int(v)
        self.startw.setdefault("elf", 172)

        # 2b. game.js 內嵌補給道具 (皮革 / 勇敢藥水 / 黃金箭 / 米索莉箭 等) + craftdb.js (44件道具 + 112配方)
        game_txt = self._read("game.js")
        try:
            inline_items = _extract_js_object(game_txt, "Object.assign(ITEMS, Object.fromEntries(Object.entries(")
            for k, v in inline_items.items():
                iid = int(k)
                if iid not in self.items:
                    self.items[iid] = v
        except Exception:
            pass
        for iid, nm in [(90074, "必成功對盔甲施法的卷軸"), (90087, "必成功對武器施法的卷軸")]:
            self.items.setdefault(iid, {"id": iid, "name": nm, "kind": "etc", "type": "scroll", "use": "zel" if iid == 90074 else "dai", "stack": 1})
        self.items.setdefault(40079, {"id": 40079, "name": "傳送回家的卷軸", "kind": "etc", "type": "scroll", "use": "home", "stack": 1})

        craft_path = self.data_dir / "craftdb.js"
        if craft_path.exists():
            try:
                cdb = _extract_js_object(craft_path.read_text(encoding="utf-8"), "CRAFTDB=")
            except Exception:
                cdb = _extract_js_object(craft_path.read_text(encoding="utf-8"), "CRAFTDB =")
            for k, v in (cdb.get("addItems") or {}).items():
                iid = int(k)
                if iid not in self.items:
                    self.items[iid] = v
            for k, v in (cdb.get("addUse") or {}).items():
                iid = int(k)
                if iid not in self.itemuse:
                    self.itemuse[iid] = str(v)
            for r in (cdb.get("recipes") or []):
                oid = int(r["out"])
                rec = {
                    "out": oid,
                    "outName": r.get("outName", ""),
                    "cat": r.get("cat", "other"),
                    "inputs": [(int(a), int(b)) for a, b in (r.get("inputs") or [])],
                    "yield": max(1, int(r.get("yield", 1))),
                    "gold": int(r.get("gold", 0)),
                    "coin": int(r.get("coin", 0)),
                    "via": r.get("via", ""),
                }
                self.recipes.append(rec)
                self.recipes_by_out.setdefault(oid, []).append(rec)

        # 與 game.js 保持一致：精靈餅乾/勇敢藥水標記為藥水類
        if 40068 in self.items:
            self.items[40068] = {**self.items[40068], "type": "potion", "use": "power"}
        if 40014 in self.items:
            self.items[40014] = {**self.items[40014], "type": "potion", "use": "power"}

        # 3. plevel.js
        pl_txt = self._read("plevel.js")
        self.expt = [int(x) for x in _extract_js_object(pl_txt, "const EXPT=")]
        self.mexp = {int(k): int(v) for k, v in _extract_js_object(pl_txt, "const MEXP=").items()}
        self.bxp = {int(k): int(v) for k, v in _extract_js_object(pl_txt, "const BXP=").items()}
        # REC 鍵為數字未加引號，用正則抓出
        rec_m = re.search(r"const REC=\{([^}]+)\}", pl_txt)
        if rec_m:
            for k, v in re.findall(r"(\d+)\s*:\s*(\d+)", rec_m.group(1)):
                self.rec[int(k)] = int(v)

        # 4. data.js
        data_obj = _extract_js_object(self._read("data.js"), "const DATA=")
        self.monsters = data_obj.get("monsters", [])
        self.monsters_by_id = {int(m["id"]): m for m in self.monsters}

        # 5. game.js (動態抽取掉落倍率、禁掉品、寶箱池、ITEMFX 特效、商店價格、關卡池與 V082 怪物卡片)
        self._parse_game_constants(game_txt)
        self._parse_item_fx(game_txt)
        self._parse_shop_anchors(game_txt)
        self._parse_stages(game_txt)
        self._parse_mcards(game_txt)
        self._compute_all_base_prices()

        # 曼孟隨機裝備箱獎池（以 GEARCATS 與有效裝備動態建構，未來加新裝自動進池）
        dyn_box = [iid for iid in self.gear_prices if self.items.get(iid, {}).get("kind") in ("weapon", "armor") and iid != 172]
        self.box_pool = dyn_box or [x for x in (180, 169, 125, 29, 47, 52, 102, 148, 20011, 20033, 20063, 20154, 20210, 20264) if x in self.items]

    def _parse_game_constants(self, game_txt: str) -> None:
        m_nd = re.search(r"NO_DROP\s*=\s*new Set\(\[([^\]]+)\]\)", game_txt)
        if m_nd:
            ids = {int(x) for x in re.findall(r"\d+", m_nd.group(1))}
            if ids:
                self.no_drop = ids
        m_ns = re.search(r"const NOSELL\s*=\s*new Set\(\[([^\]]+)\]\)", game_txt)
        if m_ns:
            ids = {int(x) for x in re.findall(r"\d+", m_ns.group(1))}
            if ids:
                self.no_sell = ids
        m_cm = re.search(r"const CHEST_MATS\s*=\s*\[([^\]]+)\]", game_txt)
        if m_cm:
            arr = [int(x) for x in re.findall(r"\d+", m_cm.group(1))]
            if arr:
                self.chest_mats = arr
        m_mk = re.search(r"const MATK\s*=\s*\{([^}]+)\}", game_txt)
        if m_mk:
            parsed: dict[int, float] = {}
            for k_s, v_s in re.findall(r"(\d+)\s*:\s*([0-9.\s/]+)", m_mk.group(1)):
                v_s = v_s.strip()
                if "/" in v_s:
                    parts = v_s.split("/", 1)
                    denom = float(parts[1].strip())
                    if denom > 0:
                        parsed[int(k_s)] = float(parts[0].strip()) / denom
                else:
                    parsed[int(k_s)] = float(v_s)
            if parsed:
                self.matk = parsed

    def _parse_item_fx(self, game_txt: str) -> None:
        m_fx = re.search(r"const ITEMFX\s*=\s*\{(.*?)\n\s*\};", game_txt, re.S)
        if not m_fx:
            return
        body = re.sub(r"/\*.*?\*/", "", m_fx.group(1))
        for iid_s, kv_s in re.findall(r"(\d+)\s*:\s*\{([^}]+)\}", body):
            fx: dict[str, float] = {}
            for k, v in re.findall(r"([A-Za-z_]+)\s*:\s*([0-9.+-]+)", kv_s):
                fx[k] = float(v)
            if fx:
                self.item_fx[int(iid_s)] = fx

    def _parse_mcards(self, game_txt: str) -> None:
        m_fx = re.search(r"const MCARD_FX\s*=\s*\{([^\n]+)\};", game_txt)
        if m_fx:
            for el_s, k_s, arr_s in re.findall(r"(\w+)\s*:\s*\[\s*'(\w+)'\s*,\s*\[([^\]]+)\]\s*\]", m_fx.group(1)):
                vals = [int(x) for x in re.findall(r"\d+", arr_s)]
                if len(vals) == 3:
                    self.mcard_fx[el_s] = (k_s, vals)

        m_sl = re.search(r"const MSOCK_SLOTS\s*=\s*\{([^}]+)\}", game_txt)
        if m_sl:
            slots: dict[str, int] = {}
            for k_s, v_s in re.findall(r"(\w+)\s*:\s*(\d+)", m_sl.group(1)):
                slots[k_s] = int(v_s)
            if slots:
                self.msock_slots = slots

        m_dm = re.search(r"const MCARD_DROP_MOB\s*=\s*([0-9.]+)\s*,\s*MCARD_DROP_BOSS\s*=\s*([0-9.]+)\s*,\s*MCARD_FUSE_RATE\s*=\s*\{([^}]+)\}", game_txt)
        if m_dm:
            self.mcard_drop_mob = float(m_dm.group(1))
            self.mcard_drop_boss = float(m_dm.group(2))
            for k_s, v_s in re.findall(r"(\d+)\s*:\s*([0-9.]+)", m_dm.group(3)):
                self.mcard_fuse_rate[int(k_s)] = float(v_s)

        m_un = re.search(r"MUNSOCK\s*=\s*\{([^\n]+)\};", game_txt)
        if m_un:
            for s_s, g_s, c_s in re.findall(r"(\d+)\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]", m_un.group(1)):
                self.mcard_unsock[int(s_s)] = (int(g_s), int(c_s))

        m_cat = re.search(r"const MCARD_CAT\s*=\s*\[(.*?)\n\s*\];", game_txt, re.S)
        if m_cat:
            cat: list[dict[str, Any]] = []
            by_id: dict[int, dict[str, Any]] = {}
            for obj_s in re.findall(r"\{[^}]+\}", m_cat.group(1)):
                id_m = re.search(r"id\s*:\s*(\d+)", obj_s)
                n_m = re.search(r"n\s*:\s*'([^']+)'", obj_s)
                el_m = re.search(r"el\s*:\s*'([^']+)'", obj_s)
                lv_m = re.search(r"lv\s*:\s*(\d+)", obj_s)
                ci_m = re.search(r"ci\s*:\s*'([^']+)'", obj_s)
                if id_m and n_m and el_m and lv_m:
                    row = {
                        "id": int(id_m.group(1)),
                        "n": n_m.group(1),
                        "el": el_m.group(1),
                        "lv": int(lv_m.group(1)),
                        "ci": ci_m.group(1) if ci_m else "",
                    }
                    cat.append(row)
                    by_id[row["id"]] = row
            if cat:
                self.mcards_cat = cat
                self.mcard_by_id = by_id

    def _parse_shop_anchors(self, game_txt: str) -> None:
        # 只錨定真正可在村莊 NPC 商店用金幣購買的清單（SHOPSELL 與 VNPC shop；不用 BUYP 以免誤壓祝捲/寶石/法書市價）
        m_ss = re.search(r"const SHOPSELL\s*=\s*(\[\[.*?\]\]);", game_txt)
        if m_ss:
            for iid, pr in json.loads(m_ss.group(1)):
                self.shop_anchor[int(iid)] = int(pr)
        for sm in re.finditer(r"shop:\s*\[([^\n;]+)\]", game_txt):
            for iid_s, pr_s in re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", sm.group(1)):
                iid, pr = int(iid_s), int(pr_s)
                if iid not in self.shop_anchor or pr < self.shop_anchor[iid]:
                    self.shop_anchor[iid] = pr

    def _parse_stages(self, game_txt: str) -> None:
        # 建立怪物名稱索引，方便解析 byNames / byNamesX
        by_name_all: dict[str, list[dict[str, Any]]] = {}
        for m in self.monsters:
            if m.get("boss") or m.get("gi"):
                continue
            by_name_all.setdefault(m.get("name", ""), []).append(m)

        # 動態蒐集所有怪物名稱陣列常數 (如 GL_POOL_D 等) 與首領變數對應 (BX(id) / monsters.find(id))
        named_pools: dict[str, list[str]] = {
            "GL_POOL_D": ["夏洛伯", "骷髏斧手", "骷髏弓箭手", "楊果里恩", "骷髏槍兵", "食屍鬼", "長者", "食人妖精"],
        }
        for pm in re.finditer(r"const ([A-Z0-9_]+)\s*=\s*\[([^\]]+)\]", game_txt):
            vn, raw_arr = pm.group(1), pm.group(2)
            strs = re.findall(r"['\"]([^'\"]+)['\"]", raw_arr)
            if strs and any(s in by_name_all for s in strs):
                named_pools[vn] = strs

        named_boss_map = {
            "BOSS": 45600,
            "BAPH": 45573,
            "UNAW": 45583,
            "OGRE": 107032,
            "DKNIGHT": 45601,
            "MAGEB": 71020,
        }
        for bm in re.finditer(r"const ([A-Z0-9_]+)\s*=\s*(?:BX\((\d+)\)|DATA\.monsters\.find\([^=]+===\s*(\d+)\))", game_txt):
            named_boss_map[bm.group(1)] = int(bm.group(2) or bm.group(3))

        m_st = re.search(r"const STAGES\s*=\s*\[(.*?)\n\s*\];", game_txt, re.S)
        if not m_st:
            return

        # 解析 game.js 的關卡樓層血攻倍率與首領覆蓋血量 (DVHP / DVDMG / BOSSHP / BOSSK)
        dvhp_k = 0.2
        k_m = re.search(r"const DVHP_K\s*=\s*([0-9.]+)", game_txt)
        if k_m:
            dvhp_k = float(k_m.group(1))
        dvhp_map: dict[int, float] = {}
        dvhp_m = re.search(r"const DVHP\s*=\s*\{([^\}]+)\}", game_txt)
        if dvhp_m:
            for km in re.finditer(r"(\d+)\s*:\s*([0-9.]+)(?:\s*\*\s*DVHP_K)?", dvhp_m.group(0)):
                val = float(km.group(2)) * (dvhp_k if "DVHP_K" in km.group(0) else 1.0)
                dvhp_map[int(km.group(1))] = val
        dvdmg_map: dict[int, float] = {}
        dvdmg_m = re.search(r"const DVDMG\s*=\s*\{([^\}]+)\}", game_txt)
        if dvdmg_m:
            for km in re.finditer(r"(\d+)\s*:\s*([0-9.]+)", dvdmg_m.group(1)):
                dvdmg_map[int(km.group(1))] = float(km.group(2))
        boss_hp_map: dict[int, int] = {}
        bh_m = re.search(r"const BOSSHP\s*=\s*\{([^\}]+)\}", game_txt)
        if bh_m:
            for km in re.finditer(r"(\d+)\s*:\s*(\d+)", bh_m.group(1)):
                boss_hp_map[int(km.group(1))] = int(km.group(2))
        boss_k = 2.0
        bk_m = re.search(r"const BOSSK\s*=\s*([0-9.]+)", game_txt)
        if bk_m:
            boss_k = float(bk_m.group(1))

        raw_stages: list[dict[str, Any] | None] = []
        by_map_idx: dict[int, int] = {}
        gm_only_maps = {252, 253, 254, 2005, 610}

        for line in m_st.group(1).splitlines():
            mid_m = re.search(r"mapId:\s*(\d+)", line)
            nm_m = re.search(r"name:\s*['\"]([^'\"]+)['\"]", line)
            if not mid_m or not nm_m:
                continue
            mid = int(mid_m.group(1))
            name = nm_m.group(1)
            is_nopool = "nopool: 1" in line

            boss_id = None
            bx_m = re.search(r"boss:\s*BX\((\d+)\)", line)
            bi_m = re.search(r"boss:\s*(\d+)\b", line)
            if bx_m:
                boss_id = int(bx_m.group(1))
            elif bi_m:
                boss_id = int(bi_m.group(1))
            else:
                bn_m = re.search(r"boss:\s*([A-Z0-9_]+)", line)
                if bn_m and bn_m.group(1) in named_boss_map:
                    boss_id = named_boss_map[bn_m.group(1)]

            if boss_id == 71014 and boss_id not in self.monsters_by_id and 45682 in self.monsters_by_id:
                boss_id = 45682

            if mid in gm_only_maps:
                raw_stages.append(None)
                continue

            rec_lv = self.rec.get(mid, 10)
            pool_ids: list[int] = []
            if not is_nopool:
                ids_m = re.search(r"byIds\(\[([^\]]+)\]\)", line)
                names_m = re.search(r"byNamesX?\(\[([^\]]+)\]\)", line)
                if ids_m:
                    pool_ids = [int(x.strip()) for x in ids_m.group(1).split(",") if x.strip().isdigit()]
                elif names_m:
                    raw_names = re.findall(r"['\"]([^'\"]+)['\"]", names_m.group(1))
                    for rn in raw_names:
                        for mon in by_name_all.get(rn, []):
                            pool_ids.append(int(mon["id"]))
                else:
                    matched_pool = next((vn for vn in named_pools if re.search(r"\b" + re.escape(vn) + r"\b", line)), None)
                    if matched_pool:
                        for rn in named_pools[matched_pool]:
                            for mon in by_name_all.get(rn, []):
                                pool_ids.append(int(mon["id"]))
                    elif re.search(r"\bMON\b", line):
                        pool_ids = [int(m["id"]) for m in self.monsters if not m.get("boss") and not m.get("gi") and m.get("lvl", 1) <= 18]
                    else:
                        # 未來新增關卡若採用其他寫法，自動依推薦等級 recLv 挑選附近一般怪進池
                        pool_ids = [int(m["id"]) for m in self.monsters if not m.get("boss") and not m.get("gi") and abs(int(m.get("lvl", 1)) - rec_lv) <= 8]
                pool_ids = [pid for pid in pool_ids if pid in self.monsters_by_id]
                if not pool_ids:
                    raw_stages.append(None)
                    continue
            elif boss_id not in self.monsters_by_id:
                raw_stages.append(None)
                continue

            dens_m = re.search(r"dens:\s*([0-9.]+)", line)
            dens_val = float(dens_m.group(1)) if dens_m else 1.0
            has_rush = bool(re.search(r"\brush:", line))
            has_waves = bool(re.search(r"\bwaves:", line))
            is_siege = bool(re.search(r"\bsiege:", line)) or (53 <= mid <= 56)

            pool_ms = [self.monsters_by_id[pid] for pid in pool_ids if pid in self.monsters_by_id]
            avg_mob_hp = (sum(max(30, int(m.get("hp", 60))) for m in pool_ms) / max(1, len(pool_ms))) if pool_ms else float(rec_lv * 8)
            avg_mob_lvl = (sum(max(1, int(m.get("lvl", rec_lv))) for m in pool_ms) / max(1, len(pool_ms))) if pool_ms else float(rec_lv)
            base_mobs = (20.0 + rec_lv * 1.15) * max(0.75, min(1.5, dens_val))
            if has_rush:
                base_mobs *= 1.25
            if has_waves:
                base_mobs *= 1.30
            if is_siege:
                base_mobs *= 1.32

            b_id = boss_id if boss_id in self.monsters_by_id else None
            b_hp_raw = 0
            b_lvl = rec_lv + 4
            if b_id:
                bmon = self.monsters_by_id[b_id]
                b_lvl = max(rec_lv + 2, int(bmon.get("lvl", rec_lv + 5)))
                b_hp_raw = boss_hp_map.get(mid, int(bmon.get("hp", 3000)))
                if not is_nopool:
                    b_hp_raw = min(200000, int(b_hp_raw * boss_k))

            st_obj = {
                "mapId": mid,
                "name": name,
                "recLv": rec_lv,
                "pool": pool_ids,
                "nopool": is_nopool,
                "bossId": b_id,
                "dens": dens_val,
                "rush": has_rush,
                "waves": has_waves,
                "siege": is_siege,
                "baseMobs": min(80.0, base_mobs),
                "avgMobHp": avg_mob_hp,
                "avgMobLv": avg_mob_lvl,
                "dvhp": dvhp_map.get(mid, 1.0),
                "dvdmg": dvdmg_map.get(mid, 1.0),
                "bossHp": max(2000, b_hp_raw),
                "bossLv": b_lvl,
            }
            by_map_idx[mid] = len(raw_stages)
            raw_stages.append(st_obj)
            self.stages.append(st_obj)

        # 解析遊戲難度表 DIFF_NAMES / DIFFT
        diff_names = ["簡單", "普通", "困難", "惡夢"]
        dn_m = re.search(r"const DIFF_NAMES\s*=\s*\[([^\]]+)\]", game_txt)
        if dn_m:
            parsed_dn = re.findall(r"['\"]([^'\"]+)['\"]", dn_m.group(1))
            if len(parsed_dn) == 4:
                diff_names = parsed_dn
        dt_m = re.search(r"const DIFFT\s*=\s*\[([^\n]+)\];", game_txt)
        hard_scale = [0.58, 1.00, 1.42, 1.98]
        if dt_m:
            parsed_dt = json.loads(re.sub(r"([a-zA-Z_]+)\s*:", r'"\1":', re.sub(r"1\s*/\s*3", "0.3333", re.sub(r"1\s*/\s*4", "0.25", "[" + dt_m.group(1) + "]"))))
            for di, row in enumerate(parsed_dt[:4]):
                self.diffs.append({
                    "idx": di,
                    "name": diff_names[di],
                    "mon": float(row.get("mon", 1.0)),
                    "boss": float(row.get("boss", 1.0)),
                    "exp": float(row.get("exp", 1.0)),
                    "drop": float(row.get("drop", 1.0)),
                    "rare": float(row.get("rare", 1.0)),
                    "bossrw": float(row.get("bossrw", 1.0)),
                    "hard": hard_scale[di],
                })
        if not self.diffs:
            self.diffs = [
                {"idx": 0, "name": "簡單", "mon": 0.3333, "boss": 0.25, "exp": 2.0, "drop": 1.0, "rare": 0.5, "bossrw": 0.5, "hard": 0.58},
                {"idx": 1, "name": "普通", "mon": 1.0, "boss": 1.0, "exp": 1.0, "drop": 1.0, "rare": 1.0, "bossrw": 1.0, "hard": 1.00},
                {"idx": 2, "name": "困難", "mon": 2.0, "boss": 2.0, "exp": 1.5, "drop": 1.3, "rare": 2.0, "bossrw": 2.0, "hard": 1.42},
                {"idx": 3, "name": "惡夢", "mon": 4.0, "boss": 4.0, "exp": 2.5, "drop": 1.8, "rare": 4.0, "bossrw": 4.0, "hard": 1.98},
            ]

        # 解析 SEGMENTS 完整流程路線（與前端選單完全一致）
        seg_m = re.search(r"const SEGMENTS\s*=\s*\{(.*?)\n\s*\};", game_txt, re.S)
        if seg_m:
            for item_m in re.finditer(r"label:\s*['\"]([^'\"]+)['\"][^}]*?s:\s*_si\((\d+)\)\s*,\s*e:\s*_si\((\d+)\)", seg_m.group(1)):
                label = item_m.group(1)
                s_mid, e_mid = int(item_m.group(2)), int(item_m.group(3))
                if s_mid in by_map_idx and e_mid in by_map_idx:
                    si, ei = by_map_idx[s_mid], by_map_idx[e_mid]
                    seg_sts = [st for st in raw_stages[si : ei + 1] if st is not None]
                    if seg_sts:
                        entry_rec = seg_sts[0]["recLv"]
                        peak_rec = max(st["recLv"] for st in seg_sts)
                        self.segments.append({
                            "label": label,
                            "stages": seg_sts,
                            "entryRec": entry_rec,
                            "peakRec": peak_rec,
                        })
        if not self.segments:
            for st in self.stages:
                self.segments.append({"label": st["name"], "stages": [st], "entryRec": st["recLv"], "peakRec": st["recLv"]})

        self.stages.sort(key=lambda s: (s["recLv"], s["mapId"]))
        self.segments.sort(key=lambda g: (g["entryRec"], g["peakRec"]))
        self.stage_mobs = {pid for s in self.stages for pid in s["pool"]} | {s["bossId"] for s in self.stages if s["bossId"]}

    def _compute_all_base_prices(self) -> None:
        # 蒐集每個道具的所有掉落來源，同時區分是否在主線 43 張地圖內可刷到
        sources_by_item: dict[int, list[tuple[int, int, bool, float, bool]]] = {}
        for m in self.monsters:
            mid = int(m["id"])
            in_stage = mid in self.stage_mobs
            lvl = max(1, int(m.get("lvl", 1)))
            hp = max(1, int(m.get("hp", 1)))
            is_boss = bool(m.get("boss"))
            for dr in m.get("drops", []):
                iid = int(dr["item"])
                if iid == GOLD_ID or iid in self.no_drop:
                    continue
                eff_ch = max(1.0, float(dr.get("chance", 10000)) * self.matk.get(iid, 1.0))
                sources_by_item.setdefault(iid, []).append((lvl, hp, is_boss, eff_ch, in_stage))

        recipe_demand: dict[int, int] = {}
        for rec in self.recipes:
            for in_id, cnt in rec["inputs"]:
                recipe_demand[in_id] = recipe_demand.get(in_id, 0) + cnt

        def calc_drop_floor(iid: int, scale: float = 0.36, cap: int = 22_000_000) -> int:
            all_srcs = sources_by_item.get(iid, [])
            if not all_srcs:
                return 0
            # 若主線地圖中有此怪，優先用主線真實來源估算（避免把無關絕版/高圖拉進來）
            srcs = [s for s in all_srcs if s[4]] or all_srcs
            best_val = None
            all_boss = all(s[2] for s in srcs)
            for lvl, hp, is_boss, eff_ch, _ in srcs:
                toughness = lvl * 1400 + (hp ** 0.52) * 1050 + (180000 if is_boss else 0)
                scarcity = min(190.0, (1_000_000.0 / eff_ch) ** 0.62)
                val = toughness * scarcity * scale
                if best_val is None or val < best_val:
                    best_val = val
            res = int(best_val or 0)
            if all_boss:
                res = int(res * 1.35)
            return min(cap, res)

        FX_VALUE = {
            "leech": 650_000,   # 1% 吸血
            "drain": 720_000,   # 1% 護盾吸收
            "exp": 450_000,     # 經驗加成
            "dr": 420_000,      # 受傷減免
            "spd": 380_000,     # 移速加成
            "hpTR": 320_000,    # 每秒回血
            "mpTR": 320_000,    # 每秒回盾
            "mag": 220_000,     # 拾取範圍
        }

        # 第一階段：武器、防具、消耗品、掉落材料、寶石、魔法書初估底價
        for iid, it in self.items.items():
            if iid == GOLD_ID:
                continue
            kind = it.get("kind")
            typ = it.get("type")
            slot = it.get("slot") or typ
            nm = it.get("name", "")
            if kind in ("weapon", "armor"):
                add = it.get("add") or {}
                stat_score = sum(abs(v) for k, v in add.items() if k != "hp") * 85000 + abs(add.get("hp", 0)) * 4500
                fx_score = 0
                for fk, fv in self.item_fx.get(iid, {}).items():
                    unit_fx = FX_VALUE.get(fk, 180_000)
                    # leech/drain/exp 等百分比以 .01/.03 為基準，hpTR/dr/mpTR 以 1 為基準
                    mult = (fv / 0.01) if fv < 0.5 else fv
                    fx_score += int(unit_fx * max(0.8, min(2.0, mult)))

                power = (
                    it.get("dmgS", 0) * 4500
                    + it.get("dmgL", 0) * 3200
                    + it.get("hit", 0) * 14000
                    + it.get("dmg", 0) * 32000
                    + abs(it.get("ac", 0)) * 13500
                    + it.get("mdef", 0) * 7500
                    + it.get("dr", 0) * 75000
                    + it.get("bowHit", 0) * 16500
                    + it.get("bowDmg", 0) * 38000
                    + stat_score
                    + fx_score
                    + (100000 if it.get("minlvl", 0) >= 30 else 0)
                )
                drop_floor = calc_drop_floor(iid, scale=0.36, cap=22_000_000)
                acc_floor = 0
                if slot in ("ring", "amulet", "earring", "belt"):
                    acc_floor = 5_500_000 if (iid in sources_by_item or slot == "earring") else 4_500_000
                elif slot == "guarder":
                    acc_floor = 3_800_000

                # 若有多隻主線低/中階小怪都能掉 (如巨斧 14 隻怪各 1%)，不要被目錄標價×2硬拉太高；無野怪掉落品保留目錄價 2.0x 保底
                srcs = [s for s in sources_by_item.get(iid, []) if s[4]]
                easy_mob_drop = sum(1 for lvl, _, is_boss, eff_ch, _ in srcs if not is_boss and lvl <= 35 and eff_ch >= 8000) >= 2
                gp_raw = self.gear_prices.get(iid, 0)
                gp = gp_raw if easy_mob_drop else gp_raw * 2

                if iid == 172:
                    p = 50
                else:
                    p = max(3500, gp, int(power), drop_floor, acc_floor)
                if iid in (20172, 20177, 20181):  # 屬性手套
                    p = max(p, 950_000)
                elif iid == 126:  # 瑪那魔杖（無野怪掉落 + 1% 吸盾續航神器）
                    p = max(p, 1_550_000)
                elif iid == 20187:  # 力量手套（0.04% 怪手 / 食人巨魔血核心製作 + exp+2%）
                    p = max(p, 2_800_000)
                elif iid == 47:  # 沉默之劍
                    p = max(p, 1_650_000)
                elif iid in (20013, 20014, 20015):  # 魔法頭盔系列
                    p = max(p, 420_000)
            elif iid in self.shop_anchor:
                p = self.shop_anchor[iid]
            elif iid == SCROLL_ARMOR_BLESSED:  # 祝防：安定內跳+1~3、過安定×1.5、衝+10唯一通道
                p = 1_450_000
            elif iid == SCROLL_WEAPON_BLESSED:  # 祝武：同上，且武器需求極大
                p = 2_600_000
            elif iid == SCROLL_ARMOR_PERFECT:
                p = 6_500_000
            elif iid == SCROLL_WEAPON_PERFECT:
                p = 12_000_000
            elif iid in (240074, 240087):
                p = 160_000
            elif iid == 40088:  # 變形卷軸
                p = 12_000
            elif iid == 40513:  # 食人巨魔的血 (MATK=1/3, 力量手套核心卡關材，目標 155~210萬)
                p = min(2_100_000, max(1_550_000, int(calc_drop_floor(iid, scale=0.42))))
            elif iid == 84072:  # 純白的萬能藥 (Boss 25% / 菁英 4% 額外掉落，10個約680萬換一瓶萬能藥)
                p = 680_000
            elif iid == 80019:  # 哈爾巴斯的執念 (Lv63+ 高階掉落)
                p = min(240_000, max(110_000, int(calc_drop_floor(iid, scale=0.16))))
            elif iid == 40524:  # 黑色血痕
                p = min(120_000, max(48_000, int(calc_drop_floor(iid, scale=0.14))))
            elif iid == 40318:  # 魔法寶石 (65張配方核心觸媒)
                p = 18_000
            elif iid == 40405:  # 皮革 (一般大量製裝原料)
                p = 2_800
            elif iid in (40014, 40068):  # 勇敢藥水 / 精靈餅乾
                p = 15_000
            elif iid == 40070:  # 進化果實
                p = 50_000
            elif iid == 40743:  # 普通箭
                p = 5
            elif iid == 40744:  # 銀箭
                p = 15
            elif iid == 40745:  # 黃金箭
                p = 50
            elif iid == 40746:  # 米索莉箭
                p = 80
            elif iid == 40017:  # 解毒藥水
                p = 450
            elif iid == 40025:  # 失明藥水
                p = 150
            elif iid in (40499, 40496):  # 蘑菇汁 / 粗糙米索莉塊 (高MATK初階大量素材)
                p = 1_200 if iid == 40499 else 1_500
            elif iid in (40507, 40519):  # 安特之樹枝 / 潘的鬃毛 (MATK=12，一場掉3~6個)
                p = 1_800
            elif iid == 40506:  # 安特的水果 (MATK=9)
                p = 2_000
            elif iid == 40515:  # 元素石 (MATK=7.5)
                p = 2_500
            elif iid == 40494:  # 純粹的米索莉塊 (MATK=2.7 + 1:1 提煉自粗糙)
                p = 3_500
            elif iid == 88:  # 潘的角 (MATK=7.5)
                p = 5_500
            elif typ == "spellbook":
                df = calc_drop_floor(iid, scale=0.22, cap=2_500_000)
                if df > 0:
                    p = max(75_000, df)
                elif iid in (40223, 49135):  # 絕對屏障 / 幻覺：化身
                    p = 650_000
                elif iid >= 49130 or iid in (40230, 40231, 40269):
                    p = 480_000
                elif iid == 40196:  # 召喚術
                    p = 180_000
                else:
                    p = 95_000
            elif typ == "scroll":
                p = max(25_000, calc_drop_floor(iid, scale=0.12, cap=400_000)) if it.get("use") == "spell" else max(8_000, calc_drop_floor(iid, scale=0.08, cap=120_000))
            elif typ == "gem" or iid in (40044, 40045, 40048, 40049, 40052):
                # 依主線地圖實際會掉落的怪物數量、最低怪等級、是否進首領寶箱與配方搶奪量，精算每顆寶石真實稀缺價
                st_srcs = [s for s in sources_by_item.get(iid, []) if s[4]] or sources_by_item.get(iid, [])
                norm_srcs = [s for s in st_srcs if not s[2]]
                src_cnt = max(1, len(st_srcs))
                min_lvl = min((s[0] for s in st_srcs), default=30)
                in_chest = iid in self.chest_mats
                demand = recipe_demand.get(iid, 0)

                base_df = calc_drop_floor(iid, scale=0.075, cap=650_000)
                # 來源越少 (如高品藍主線僅3隻)、入門怪等級越高 (Lv.30+/40+)、且首領箱不會開出者，價格大幅拉開
                scarcity_mul = 1.0
                if src_cnt <= 3:
                    scarcity_mul *= 2.05
                elif src_cnt <= 6:
                    scarcity_mul *= 1.72
                elif src_cnt >= 13:
                    scarcity_mul *= 0.65
                if min_lvl >= 38:
                    scarcity_mul *= 1.45
                elif min_lvl >= 28:
                    scarcity_mul *= 1.38
                elif min_lvl <= 16 and len(norm_srcs) >= 4:
                    scarcity_mul *= 0.68
                if in_chest:
                    scarcity_mul *= 0.72

                if "高品質" in nm:
                    tier_floor = 120_000
                elif "品質" in nm:
                    tier_floor = 32_000
                else:
                    tier_floor = 18_000
                demand_add = demand * (14_000 if "高品質" in nm else 7_500 if "品質" in nm else 3_000)
                raw_p = int((max(tier_floor, base_df) * scarcity_mul) + demand_add)
                p = min(950_000 if "高品質" in nm else 360_000 if "品質" in nm else 90_000, max(tier_floor, raw_p))
            elif typ == "material":
                df = calc_drop_floor(iid, scale=0.05, cap=380_000)
                matk = self.matk.get(iid, 1.0)
                demand_bonus = 0 if matk >= 2.0 else min(8_000, recipe_demand.get(iid, 0) * 180)
                if "奧里哈魯根" in nm or "金屬板" in nm:
                    floor = 18_000
                else:
                    floor = 3_500
                p = max(floor + demand_bonus, df)
            elif typ == "potion":
                p = max(1_200, calc_drop_floor(iid, scale=0.04, cap=35_000))
            elif typ == "food" or "煙火" in nm or "仙女棒" in nm or iid in (40001, 40002, 40003, 40005, 40315):
                p = 90
            elif str(iid).startswith("81"):
                p = 260_000
            else:
                df = calc_drop_floor(iid, scale=0.06, cap=180_000)
                p = max(500, df)
            self.market_base[iid] = max(5, int(round(p / 10.0) * 10) if p >= 100 else int(p))

        # 第二階段：沿 CRAFTDB 112 張配方做由下而上成本傳導（合成料與成品裝價格貼合真實成本，避免中間料過度滾雪球）
        coin_val = 45_000
        for _pass in range(4):
            for rec in self.recipes:
                oid = rec["out"]
                if oid not in self.items or oid in self.no_sell or oid in (40068, 40494):
                    continue
                in_cost = sum(self.market_base.get(in_id, 2_000) * cnt for in_id, cnt in rec["inputs"])
                total_cost = in_cost + rec["gold"] + rec["coin"] * coin_val
                unit_cost = total_cost / max(1, rec["yield"])
                it = self.items[oid]
                kind = it.get("kind")
                has_mob_drop = oid in sources_by_item
                if not has_mob_drop and kind == "etc":
                    # 純製作中間素材：加上合理工本溢價 (1.06x)
                    target_p = int(round((unit_cost * 1.06) / 10.0) * 10)
                    self.market_base[oid] = max(self.market_base.get(oid, 500), target_p)
                elif kind in ("weapon", "armor"):
                    # 僅針對純製作或極稀有掉落裝備強制高於材料成本；一般普怪會大量掉落的裝 (如巨斧/戰斧) 不受過高配方成本綁架
                    st_eq_srcs = [s for s in sources_by_item.get(oid, []) if s[4]]
                    easy_eq = any(not is_b and lv <= 35 and ch >= 8000 for lv, _, is_b, ch, _ in st_eq_srcs)
                    if not easy_eq:
                        target_p = int(round((unit_cost * 1.15) / 10.0) * 10)
                        if target_p > self.market_base.get(oid, 0):
                            self.market_base[oid] = target_p

    def can_equip(self, cls: str, iid: int) -> bool:
        it = self.items.get(iid)
        if not it:
            return False
        kind = it.get("kind")
        if kind not in ("weapon", "armor"):
            return False
        if cls == "elf":
            if not it.get("elf"):
                return False
            if kind == "weapon" and it.get("type") != "bow":
                return False
        u = self.itemuse.get(iid)
        if u and cls != "elf":
            # itemuse 順序：royal(0), knight(1), mage(2), elf(3), darkelf(4), dk(5), ill(6)
            order = ["royal", "knight", "mage", "elf", "darkelf", "dk", "ill"]
            idx = order.index(cls)
            if u[idx] == "0":
                return False
        if kind == "weapon":
            if it.get("type") not in CLASS_WEAPONS.get(cls, set()):
                return False
        return True

    def item_power_score(self, cls: str, iid: int, ench: int = 0) -> float:
        it = self.items.get(iid)
        if not it:
            return -1.0
        kind = it.get("kind")
        add = it.get("add") or {}
        if kind == "weapon":
            base = (it.get("dmgS", 0) + it.get("dmgL", 0)) * 0.5 + it.get("dmg", 0) * 1.4 + it.get("hit", 0) * 0.7
            if cls in ("mage", "ill"):
                base += add.get("sp", 0) * 5.0 + add.get("int", 0) * 2.5
            elif cls == "elf":
                base += it.get("bowDmg", 0) * 1.8 + it.get("bowHit", 0) * 0.9
            fx = self.item_fx.get(iid, {})
            fx_pts = fx.get("leech", 0) * 450 + fx.get("drain", 0) * 500 + fx.get("exp", 0) * 120 + fx.get("spd", 0) * 100 + fx.get("hpTR", 0) * 2.5 + fx.get("mpTR", 0) * 2.5
            return base + ench * 2.1 + sum(add.values()) * 1.2 + fx_pts
        if kind == "armor":
            ac_pts = abs(it.get("ac", 0)) + ench
            fx = self.item_fx.get(iid, {})
            fx_pts = fx.get("dr", 0) * 4.0 + fx.get("exp", 0) * 100 + fx.get("spd", 0) * 80 + fx.get("hpTR", 0) * 2.5 + fx.get("mpTR", 0) * 2.5
            return (
                ac_pts * 2.0
                + it.get("dr", 0) * 5.0
                + it.get("mdef", 0) * 0.6
                + it.get("bowDmg", 0) * (3.5 if cls == "elf" else 0.2)
                + it.get("bowHit", 0) * (2.0 if cls == "elf" else 0.1)
                + it.get("dmg", 0) * (3.0 if cls != "elf" else 0.5)
                + add.get("hp", 0) * 0.08
                + sum(v for k, v in add.items() if k != "hp") * 2.5
                + fx_pts
            )
        return 0.0


def _expected_ench_price(db: GameDatabase, iid: int, ench: int) -> int:
    base = db.market_base.get(iid, 1000)
    if ench <= 0:
        return base
    it = db.items.get(iid)
    if not it:
        return base
    safe = max(0, int(it.get("safe", 4)))
    kind = it.get("kind", "armor")
    scr_cost = db.market_base.get(SCROLL_WEAPON_NORMAL if kind == "weapon" else SCROLL_ARMOR_NORMAL, 50000)
    val = float(base)
    for step_e in range(ench):
        if step_e < safe:
            val += scr_cost * 1.08
        else:
            p = max(0.05, ench_rate(kind, step_e, safe, blessed=(step_e >= ENCH_MAX - 1)))
            # safe=0 的飾品若完全照連乘阻尼容易暴衝到數十億，改採溫和飾品溢價曲線
            exp_damp = 0.42 if safe == 0 else 0.72
            val = (val + scr_cost) / (p ** exp_damp)
    return max(base, int(round(val / 10.0) * 10))


def calc_expected_price(db: GameDatabase, iid: int, ench: int, market: dict[str, int]) -> int:
    key = f"{iid}_{ench}"
    if key in market:
        return market[key]
    res = _expected_ench_price(db, iid, ench)
    if ench > 0:
        for prev_e in range(ench - 1, -1, -1):
            pk = f"{iid}_{prev_e}"
            if pk in market:
                floor_p = int(round((market[pk] * (1.25 ** (ench - prev_e))) / 5.0) * 5)
                if res < floor_p:
                    res = floor_p
                break
    market[key] = res
    return res


def item_event_cat(db: GameDatabase, iid: int) -> str:
    it = db.items.get(iid, {})
    kind = it.get("kind")
    if kind == "weapon":
        return "weapon"
    if kind == "armor":
        return "armor"
    t = it.get("type")
    if t == "gem" or 40044 <= iid <= 40055:
        return "gem"
    if t == "scroll" or iid in (40074, 40087, 140074, 140087, 90074, 90087, 40079):
        return "scroll"
    if t == "spellbook":
        return "spellbook"
    if t in ("potion", "arrow") or iid in (40010, 40011, 40012, 40014, 40068, 40743):
        return "potion"
    if t == "material" or iid in (88, 89, 100, 80019, 84072):
        return "material"
    return "etc"


def item_event_mult(db: GameDatabase, state: dict[str, Any], iid: int) -> float:
    ev = state.get("dailyEvent")
    if not ev:
        return 1.0
    mults = ev.get("mults") or {}
    cat = item_event_cat(db, iid)
    return float(mults.get(cat, mults.get("all", 1.0)))


def calc_live_price(db: GameDatabase, iid: int, ench: int, market: dict[str, int], state: dict[str, Any]) -> int:
    base_p = calc_expected_price(db, iid, ench, market)
    mult = item_event_mult(db, state, iid)
    return max(15, int(round(base_p * mult / 5.0) * 5))


def _parse_yahoo_chart(res_item: dict[str, Any]) -> tuple[float, dt.date, float, float] | None:
    """解析 Yahoo Finance chart 回應，依市場本地交易日去重並結合 regularMarketPrice"""
    meta = res_item.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    timestamps = res_item.get("timestamp") or []
    closes_raw = ((res_item.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    daily_closes: dict[dt.date, float] = {}
    for ts, c in zip(timestamps, closes_raw):
        if ts is None or c is None:
            continue
        c_val = float(c)
        if c_val <= 0:
            continue
        d = dt.datetime.fromtimestamp(int(ts) + gmtoffset, dt.timezone.utc).date()
        daily_closes[d] = c_val

    mkt_ts = float(meta.get("regularMarketTime") or 0)
    mkt_price = float(meta.get("regularMarketPrice") or 0)
    if mkt_ts > 0 and mkt_price > 0:
        mkt_date = dt.datetime.fromtimestamp(int(mkt_ts) + gmtoffset, dt.timezone.utc).date()
        daily_closes[mkt_date] = mkt_price

    dates = sorted(daily_closes.keys())
    if len(dates) < 2:
        return None
    prev_d, cur_d = dates[-2], dates[-1]
    prev_c, cur_c = daily_closes[prev_d], daily_closes[cur_d]
    ret = (cur_c - prev_c) / prev_c
    return ret, cur_d, cur_c, mkt_ts


def _fetch_yahoo_daily_return(symbol: str, timeout: float = 5.0) -> tuple[float | None, float | None, str]:
    """抓取 Yahoo Finance 最新兩個有效交易日計算漲跌幅並回傳即時點數（不計算休市、直接取最新行情）"""
    best: tuple[float, dt.date, float, float] | None = None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            res = (data.get("chart") or {}).get("result") or []
            if not res:
                continue
            parsed = _parse_yahoo_chart(res[0])
            if parsed is not None:
                if best is None or (parsed[1], parsed[3]) > (best[1], best[3]):
                    best = parsed
        except Exception:
            continue
    if best is None:
        return None, None, "無資料"
    ret, cur_d, cur_c, _ = best
    return ret, cur_c, f"{cur_c:,.2f} ({ret * 100:+.2f}% · {cur_d.month}/{cur_d.day})"


def fetch_real_world_signals(timeout: float = 5.0) -> dict[str, Any]:
    global _LIVE_SIGNAL_CACHE
    if _LIVE_SIGNAL_CACHE is not None:
        return _LIVE_SIGNAL_CACHE

    temp_c = 22.0
    precip = 0.0
    wcode = 3
    w_ok = False
    try:
        w_url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=25.033&longitude=121.5654"
            "&current=temperature_2m,precipitation,weather_code&timezone=Asia%2FTaipei"
        )
        req = urllib.request.Request(w_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            wd = json.loads(resp.read().decode("utf-8"))
        cur = wd.get("current") or {}
        if "temperature_2m" in cur:
            temp_c = float(cur["temperature_2m"])
            precip = float(cur.get("precipitation") or 0.0)
            wcode = int(cur.get("weather_code") or 0)
            w_ok = True
    except Exception:
        pass

    tw_ret, tw_price, tw_lbl = None, None, "無資料"
    us_ret, us_price, us_lbl = None, None, "無資料"
    try:
        tw_ret, tw_price, tw_lbl = _fetch_yahoo_daily_return("%5ETWII", timeout=timeout)
    except Exception:
        pass
    try:
        us_ret, us_price, us_lbl = _fetch_yahoo_daily_return("%5EGSPC", timeout=timeout)
    except Exception:
        pass

    rets = [r for r in (tw_ret, us_ret) if r is not None]
    avg_ret = weighted_stock_ret(tw_ret, us_ret)
    _LIVE_SIGNAL_CACHE = {
        "liveWeather": w_ok,
        "tempC": temp_c,
        "precip": precip,
        "wcode": wcode,
        "twRet": tw_ret,
        "usRet": us_ret,
        "twPrice": tw_price,
        "usPrice": us_price,
        "twLabel": tw_lbl,
        "usLabel": us_lbl,
        "avgRet": avg_ret,
        "liveStocks": bool(rets),
    }
    return _LIVE_SIGNAL_CACHE


def weighted_stock_ret(tw_ret: float | None, us_ret: float | None) -> float:
    """取波動較強市場佔 70%、另一方佔 30%，避免一漲一跌過度互相抵銷"""
    if tw_ret is not None and us_ret is not None:
        return (tw_ret * 0.70 + us_ret * 0.30) if abs(tw_ret) >= abs(us_ret) else (us_ret * 0.70 + tw_ret * 0.30)
    if tw_ret is not None:
        return tw_ret
    if us_ret is not None:
        return us_ret
    return 0.0


def calc_stock_mult(avg_ret: float) -> float:
    """三段式股市曲線（0~1% 靈敏 ×10、1~3% ×4、3~10% ×1.5，最高 ±28.5%）"""
    a = abs(avg_ret)
    if a <= 0.01:
        delta = a * 10.0
    elif a <= 0.03:
        delta = 0.10 + (a - 0.01) * 4.0
    else:
        delta = min(0.285, 0.18 + (a - 0.03) * 1.5)
    return round(1.0 + delta if avg_ret >= 0 else 1.0 - delta, 4)


def weather_theme_mults(temp_c: float, precip: float, wcode: int) -> tuple[str, dict[str, float]]:
    """台北真實天氣次輔：偏多頭題材小加持 (+4%~+8%)，主波動仍交給事件卡與股市"""
    if precip >= 3.0 or wcode >= 80:
        return f"⛈️ 台北豪雨 ({temp_c:.1f}°C)", {"gem": 1.08, "armor": 1.05, "potion": 1.04}
    if precip > 0.1 or (51 <= wcode <= 77):
        return f"🌧️ 台北降雨 ({temp_c:.1f}°C)", {"armor": 1.06, "scroll": 1.06}
    if temp_c >= 29.0:
        return f"☀️ 台北高溫 ({temp_c:.1f}°C)", {"potion": 1.07}
    if temp_c <= 16.0:
        return f"❄️ 台北低溫 ({temp_c:.1f}°C)", {"weapon": 1.06, "scroll": 1.05, "material": 1.05}
    return f"🌤️ 台北晴陰 ({temp_c:.1f}°C)", {}


def _apply_event_signals(
    ev: dict[str, Any],
    card_mults: dict[str, float],
    temp_c: float,
    precip: float,
    wcode: int,
    avg_ret: float,
    tw_lbl: str,
    us_lbl: str,
    tw_price: float | None = None,
    us_price: float | None = None,
) -> None:
    """依事件卡底率 × 即時台美股 × 台北天氣更新 dailyEvent 的倍率與顯示來源"""
    stock_mult = calc_stock_mult(avg_ret)
    w_label, w_mults = weather_theme_mults(temp_c, precip, wcode)
    resolved: dict[str, float] = {}
    for cat in EVENT_CATS:
        m_card = float(card_mults.get(cat, 1.0))
        m_weath = float(w_mults.get(cat, 1.0))
        final_m = max(0.39, min(1.59, m_card * stock_mult * m_weath))
        resolved[cat] = round(final_m, 3)

    stock_pct = (stock_mult - 1.0) * 100
    ev["cardMults"] = card_mults
    ev["stockMult"] = round(stock_mult, 3)
    ev["weatherLabel"] = w_label
    ev["weatherMults"] = w_mults
    ev["mults"] = resolved
    ev["source"] = f"台股 {tw_lbl} · 美股 {us_lbl} (大盤輔 {stock_pct:+.1f}%) ｜ {w_label}"
    if tw_price is not None:
        ev["twIndex"] = round(tw_price, 2)
    if us_price is not None:
        ev["usIndex"] = round(us_price, 2)


def refresh_daily_event_signals(state: dict[str, Any]) -> dict[str, Any] | None:
    """不因 tick 換卡週期限制，每次執行皆重抓即時台美股（含即時點數）與台北天氣"""
    ev = state.get("dailyEvent")
    if not isinstance(ev, dict) or not ev.get("id"):
        return None
    sig = fetch_real_world_signals(timeout=5.0)
    if not sig.get("liveStocks") and not sig.get("liveWeather"):
        return ev

    card_mults = ev.get("cardMults")
    if not isinstance(card_mults, dict):
        old_stock = float(ev.get("stockMult") or 1.0) or 1.0
        old_w = ev.get("weatherMults") or {}
        old_mults = ev.get("mults") or {}
        card_base = next((c.get("mults", {}) for c in DAILY_EVENT_POOL if c["id"] == ev["id"]), {})
        def_m = float(card_base.get("all", 1.0))
        recovered: dict[str, float] = {}
        for cat in EVENT_CATS:
            base_c = float(card_base.get(cat, def_m))
            if cat in old_mults and old_stock > 0:
                est = float(old_mults[cat]) / (old_stock * float(old_w.get(cat, 1.0)))
                recovered[cat] = round(max(0.55, min(1.15, est)), 4)
            else:
                recovered[cat] = round(max(0.55, min(1.15, base_c)), 4)
        card_mults = recovered

    if sig.get("liveStocks"):
        avg_ret = float(sig["avgRet"])
        tw_lbl, us_lbl = sig["twLabel"], sig["usLabel"]
        tw_price, us_price = sig.get("twPrice"), sig.get("usPrice")
    else:
        # 股市 API 暫時無法連線時保留既有股票數值，不覆蓋為無資料
        old_sm = float(ev.get("stockMult") or 1.0)
        avg_ret = 0.0
        if old_sm != 1.0:
            d = abs(old_sm - 1.0)
            avg_ret = (d / 10.0) if d <= 0.10 else ((0.01 + (d - 0.10) / 4.0) if d <= 0.18 else (0.03 + (d - 0.18) / 1.5))
            if old_sm < 1.0:
                avg_ret = -avg_ret
        src_old = str(ev.get("source") or "")
        m_tw = re.search(r"台股\s+([^·]+)", src_old)
        m_us = re.search(r"美股\s+([^(｜]+)", src_old)
        tw_lbl = m_tw.group(1).strip() if m_tw else "無資料"
        us_lbl = m_us.group(1).strip() if m_us else "無資料"
        tw_price, us_price = ev.get("twIndex"), ev.get("usIndex")

    _apply_event_signals(
        ev,
        card_mults,
        float(sig["tempC"]),
        float(sig["precip"]),
        int(sig["wcode"]),
        avg_ret,
        tw_lbl,
        us_lbl,
        tw_price=tw_price,
        us_price=us_price,
    )
    return ev


def roll_daily_event(state: dict[str, Any], rng: random.Random, tick_id: int, allow_live: bool = False) -> dict[str, Any]:
    """三層疊乘物價公式：20張突發事件卡(主 +15%~-45%) × 台美股(輔三段式 ±28.5%) × 台北天氣(次輔 +4%~+8%)"""
    recent: list[str] = state.setdefault("recentEventIds", [])
    cands = [c for c in DAILY_EVENT_POOL if c["id"] not in recent[-5:]] or DAILY_EVENT_POOL
    card = rng.choice(cands)
    recent.append(card["id"])
    if len(recent) > 12:
        state["recentEventIds"] = recent[-12:]

    jitter = rng.uniform(0.96, 1.04)
    base_map = card["mults"]
    default_card_m = float(base_map.get("all", 1.0))
    card_mults: dict[str, float] = {
        cat: round(max(0.55, min(1.15, float(base_map.get(cat, default_card_m)) * jitter)), 4)
        for cat in EVENT_CATS
    }

    tw_price: float | None = None
    us_price: float | None = None
    if allow_live:
        sig = fetch_real_world_signals(timeout=5.0)
        temp_c, precip, wcode = sig["tempC"], sig["precip"], sig["wcode"]
        avg_ret = sig["avgRet"]
        tw_lbl, us_lbl = sig["twLabel"], sig["usLabel"]
        tw_price, us_price = sig.get("twPrice"), sig.get("usPrice")
    else:
        # 大量歷史預演不洗外部 API，使用逼真亂數氣候＋股價分布
        r_w = rng.random()
        if r_w < 0.10:
            temp_c, precip, wcode = rng.uniform(20, 27), rng.uniform(3.5, 12.0), 95
        elif r_w < 0.25:
            temp_c, precip, wcode = rng.uniform(18, 25), rng.uniform(0.3, 2.5), 61
        elif r_w < 0.45:
            temp_c, precip, wcode = rng.uniform(29.2, 34.5), 0.0, 1
        elif r_w < 0.60:
            temp_c, precip, wcode = rng.uniform(11.0, 15.8), 0.0, 2
        else:
            temp_c, precip, wcode = rng.uniform(18.0, 27.5), 0.0, 3
        if rng.random() < 0.28:
            tw_ret, us_ret = 0.0, 0.0
            tw_lbl, us_lbl = "平盤", "平盤"
        else:
            tw_ret = max(-0.045, min(0.045, rng.gauss(0.0, 0.013)))
            us_ret = max(-0.045, min(0.045, rng.gauss(0.0, 0.013)))
            tw_lbl, us_lbl = f"{tw_ret * 100:+.2f}%", f"{us_ret * 100:+.2f}%"
        avg_ret = weighted_stock_ret(tw_ret, us_ret)

    ev: dict[str, Any] = {
        "id": card["id"],
        "title": card["title"],
        "desc": card["desc"],
        "sellUrgency": float(card.get("sellUrgency", 1.0)),
        "taxRate": float(card.get("taxRate", 0.10)),
        "scalperBuyMax": float(card.get("scalperBuyMax", 0.80)),
        "enchantBonus": int(card.get("enchantBonus", 0)),
        "forceHard": bool(card.get("forceHard", False)),
        "startTick": tick_id,
        "untilTick": tick_id + 23,
    }
    _apply_event_signals(
        ev,
        card_mults,
        temp_c,
        precip,
        wcode,
        avg_ret,
        tw_lbl,
        us_lbl,
        tw_price=tw_price,
        us_price=us_price,
    )
    state["dailyEvent"] = ev
    return ev


def apply_merchant_loss_event(
    npcs: list[dict[str, Any]], state: dict[str, Any], rng: random.Random, tick_id: int, ticker: list[str]
) -> None:
    """奇岩商人每 3~5 天 (72~120 ticks) 遭遇鉅額虧損風險（不破產、保留至少 50 萬營運金）"""
    next_loss = state.get("nextMerchantLossTick")
    if next_loss is None:
        state["nextMerchantLossTick"] = tick_id + rng.randint(72, 120)
        return
    if tick_id < next_loss:
        return

    cands = [n for n in npcs if n["tier"] == "scalper" and n.get("ageTicks", 0) >= 72 and n["gold"] >= 3_000_000]
    ev = state.get("dailyEvent") or {}
    avg_m = sum((ev.get("mults") or {}).values()) / max(1, len(ev.get("mults") or {})) if ev.get("mults") else 1.0
    bearish = avg_m < 0.90

    if cands:
        n_hit = 2 if (bearish and len(cands) >= 2 and rng.random() < 0.55) else 1
        # 資產越高的富商越容易成為目標
        weights = [max(1.0, float(n["gold"])) for n in cands]
        picked: list[dict[str, Any]] = []
        pool = list(zip(cands, weights))
        for _ in range(min(n_hit, len(pool))):
            chosen = rng.choices([x[0] for x in pool], weights=[x[1] for x in pool], k=1)[0]
            picked.append(chosen)
            pool = [x for x in pool if x[0]["id"] != chosen["id"]]

        stories = [
            "遠洋商船遇暴風沉沒，囤貨大筆賠付",
            "高價接盤遇到行情反轉慘遭套牢",
            "違規走私被奇岩商會重罰查扣保證金",
            "合夥投資古龍期貨槓桿爆倉認賠",
        ]
        for m_npc in picked:
            loss_pct = rng.uniform(0.45, 0.70) if bearish else rng.uniform(0.30, 0.65)
            old_g = m_npc["gold"]
            new_g = max(500_000, int(old_g * (1.0 - loss_pct)))
            lost = old_g - new_g
            if lost > 0:
                m_npc["gold"] = new_g
                state["merchantLossCount"] = state.get("merchantLossCount", 0) + 1
                story = rng.choice(stories)
                m_npc["lastEvent"] = f"{story}，痛失 {lost:,} 金幣…"
                ticker.append(f"💸【商會鉅虧】「{m_npc['name']}」{story}，資產蒸發 {lost:,} 金幣！")

    state["nextMerchantLossTick"] = tick_id + rng.randint(72, 120)


def npc_base_hp_mp(cls: str, lv: int, cs: dict[str, int]) -> tuple[int, int]:
    ct = {"royal": 0, "knight": 1, "elf": 2, "mage": 3, "darkelf": 4, "dk": 5, "ill": 6}[cls]
    hpb = [11, 17, 10, 7, 10, 13, 9][ct]
    maxhp = [2800, 3200, 2400, 2000, 2400, 2600, 2200][ct]
    maxmp = [1000, 800, 1600, 2400, 1200, 1200, 2200][ct]
    con = cs.get("con", 12)
    wis = cs.get("wis", 12)
    y = 2 if (wis < 9 or (9 < wis < 12)) else 3 if (wis == 9 or 12 <= wis <= 17) else 4 if (18 <= wis <= 23 or wis in (25, 26, 29, 30, 33, 34)) else 5
    z = 0 if wis <= 9 else 1 if wis <= 14 else 2 if wis <= 20 else 3 if wis <= 24 else 4 if wis <= 28 else 5 if wis <= 32 else 6
    mp_mult = [1.0, 2 / 3, 1.5, 2.0, 1.5, 2 / 3, 5 / 3][ct]
    hp_g = min(maxhp - 100, int((lv - 1) * (max(0, con - 15) + hpb + 0.6)))
    mp_g = min(maxmp - 40, int((lv - 1) * (((y + 1) / 2 + z) * mp_mult)))
    return max(0, hp_g), max(0, mp_g)


def pick_unused_name(used: set[str], rng: random.Random, tier: str = "regular") -> str:
    pool = SCALPER_NAMES if tier == "scalper" else NPC_NAME_POOL
    avail = [n for n in pool if n not in used]
    if not avail:
        avail = [n for n in NPC_NAME_POOL if n not in used]
    if avail:
        nm = rng.choice(avail)
        used.add(nm)
        return nm
    for i in range(1, 999):
        cand = f"{rng.choice(pool)}{i}"
        if cand not in used:
            used.add(cand)
            return cand
    return f"冒險者{rng.randint(1000,9999)}"


def create_new_npc(
    idx: int | str,
    cls: str,
    tier: str,
    used_names: set[str],
    db: GameDatabase,
    rng: random.Random,
    whale_retire: bool = False,
) -> dict[str, Any]:
    name = pick_unused_name(used_names, rng, tier)
    base_st = dict(CLASS_BASE_STATS[cls])
    for k in base_st:
        base_st[k] = max(7, min(20, base_st[k] + rng.choice([-1, 0, 0, 1])))
    start_w = db.startw.get(cls, 172)
    eq = {"weapon": {"id": start_w, "ench": 0, "n": 1}}
    gold = 0
    npc_id = idx if isinstance(idx, str) and idx.startswith("npc_") else f"npc_{int(idx):02d}"

    npc_obj: dict[str, Any] = {
        "id": npc_id,
        "name": name,
        "cls": cls,
        "sex": rng.randint(0, 1),
        "tier": tier,
        "lv": 1,
        "exp": 0,
        "gold": gold,
        "kills": 0,
        "runs": 0,
        "ageTicks": 0,
        "maxLvTicks": 0,
        "stats": base_st,
        "eq": eq,
        "bag": {},  # { "itemId_ench": count }
        "shop": [],  # list of listing dicts
        "card": init_npc_card(tier, 1, rng),
        "mcards": {"own": {}, "sock": {}},
        "combatLog": [],
        "lastEvent": f"剛加入奇岩冒險的小{CLS_ZH[cls]}",
        "state": "village",
    }
    if tier in WHALE_RETIRE_QUOTAS and whale_retire:
        npc_obj["whaleRetire"] = True
    return npc_obj


def ensure_whale_retire_elites(
    npcs: list[dict[str, Any]], quotas: dict[str, int] | None = None
) -> None:
    """確保指定職業層（商人 2、一般 4、新手 3）套用肝帝退坑機制（不吃年齡與35~45等退坑）"""
    q = quotas if quotas is not None else WHALE_RETIRE_QUOTAS
    for n in npcs:
        if n.get("tier") not in q and n.get("whaleRetire"):
            n.pop("whaleRetire", None)
    for tier_key, target in q.items():
        members = [n for n in npcs if n.get("tier") == tier_key]
        marked = [n for n in members if n.get("whaleRetire")]
        if len(marked) > target:
            for extra in marked[target:]:
                extra.pop("whaleRetire", None)
        elif len(marked) < target:
            need = target - len(marked)
            cands = [n for n in members if not n.get("whaleRetire")]
            cands.sort(key=lambda n: (-int(n.get("gold", 0)), -int(n.get("lv", 0)), str(n.get("id", ""))))
            for n in cands[:need]:
                n["whaleRetire"] = True


def init_npc_card(tier: str, lv: int, rng: random.Random) -> dict[str, str]:
    """初始化或為舊存檔補齊變身圖鑑卡"""
    if lv <= 6:
        ct = "green" if (tier == "whale" and rng.random() < 0.35) else "white"
    elif lv <= 18:
        ct = rng.choice(["green", "blue"]) if tier == "whale" else ("green" if rng.random() < 0.55 else "white")
    elif lv <= 32:
        if tier == "whale":
            ct = rng.choice(["blue", "red", "purple"])
        elif tier == "regular":
            ct = rng.choice(["green", "blue", "blue", "red"])
        else:
            ct = rng.choice(["white", "green", "blue"])
    else:
        if tier == "whale":
            ct = rng.choice(["red", "purple", "gold"])
        elif tier == "regular":
            ct = rng.choice(["blue", "red", "red", "purple"])
        else:
            ct = rng.choice(["green", "blue", "red"])
    return {"tier": ct, "zh": CARD_TIERS[ct]["zh"], "name": rng.choice(CARD_TIERS[ct]["names"])}


def maybe_upgrade_npc_card(npc: dict[str, Any], rng: random.Random, boost: float = 1.0) -> dict[str, str]:
    """隨冒險場數、打王與開箱慢慢進階變身圖鑑卡（白→綠→藍→紅→紫→金）"""
    card = npc.get("card")
    if not isinstance(card, dict) or card.get("tier") not in CARD_TIERS:
        card = init_npc_card(npc["tier"], npc["lv"], rng)
        npc["card"] = card
    cur = card["tier"]
    idx = CARD_ORDER.index(cur)
    if idx >= len(CARD_ORDER) - 1:
        return card
    tier = npc["tier"]
    lv = npc["lv"]
    # 每個等級區間的卡階突破門檻與單次機率
    tier_bonus = 1.8 if tier == "whale" else 1.15 if tier == "regular" else 0.9 if tier == "scalper" else 0.72
    min_lv = [1, 8, 16, 25, 36][idx]
    if lv < min_lv and boost <= 1.2:
        return card
    base_p = [0.055, 0.032, 0.016, 0.0085, 0.004][idx]
    p = min(0.40, base_p * tier_bonus * boost)
    if rng.random() < p:
        nxt = CARD_ORDER[idx + 1]
        npc["card"] = {"tier": nxt, "zh": CARD_TIERS[nxt]["zh"], "name": rng.choice(CARD_TIERS[nxt]["names"])}
    return npc["card"]


def _mcard_unsock_gold(star: int, db: GameDatabase) -> int:
    g, coin = db.mcard_unsock.get(star, (100_000, 5))
    coin_price = db.shop_anchor.get(49100, 10_000)
    return g + coin * coin_price


def _mcard_score(cls: str, el: str, star: int, lv: int) -> float:
    base = float(star) * 100.0
    if el == "fire":
        w = 34.0 if cls in ("mage", "ill", "darkelf") else 30.0
    elif el == "wind":
        w = 24.0 if cls in ("elf", "darkelf") else 20.0
    elif el == "earth":
        w = 24.0 if cls in ("knight", "dk", "royal") else 17.0
    elif el == "water":
        w = 21.0 if cls in ("mage", "ill", "elf") else 16.0
    else:  # none: exp%
        w = 22.0 if lv < 42 else 11.0
    return base + w


def npc_mcard_bonuses(npc: dict[str, Any], db: GameDatabase) -> dict[str, float]:
    out = {"dmgpct": 0.0, "shield": 0.0, "hit": 0.0, "hp": 0.0, "exppct": 0.0}
    mc = npc.get("mcards")
    if not isinstance(mc, dict):
        return out
    sock = mc.get("sock") or {}
    eq = npc.get("eq") or {}
    for slot, cards in sock.items():
        if slot not in eq or slot not in db.msock_slots or not isinstance(cards, list):
            continue
        lim = db.msock_slots[slot]
        for c in cards[:lim]:
            if not isinstance(c, dict):
                continue
            mid = int(c.get("m", 0))
            star = max(1, min(3, int(c.get("s", 1))))
            info = db.mcard_by_id.get(mid)
            if not info:
                continue
            fx = db.mcard_fx.get(info["el"])
            if not fx:
                continue
            stat_k, vals = fx
            out[stat_k] = out.get(stat_k, 0.0) + float(vals[star - 1])
    return out


def npc_mcard_summary(npc: dict[str, Any]) -> dict[str, int]:
    mc = npc.get("mcards") or {}
    own = mc.get("own") or {}
    sock = mc.get("sock") or {}
    eq = npc.get("eq") or {}
    types: set[str] = set()
    c1 = c2 = c3 = 0
    for mid_s, arr in own.items():
        if not isinstance(arr, list) or len(arr) < 3:
            continue
        n1, n2, n3 = int(arr[0]), int(arr[1]), int(arr[2])
        if n1 + n2 + n3 > 0:
            types.add(str(mid_s))
            c1 += n1
            c2 += n2
            c3 += n3
    for slot, cards in sock.items():
        if slot not in eq or not isinstance(cards, list):
            continue
        for c in cards:
            if not isinstance(c, dict):
                continue
            types.add(str(c.get("m", 0)))
            s = int(c.get("s", 1))
            if s == 3:
                c3 += 1
            elif s == 2:
                c2 += 1
            else:
                c1 += 1
    return {"t": len(types), "n": c1, "s": c2, "g": c3}


def roll_npc_mcard_drops(
    npc: dict[str, Any],
    db: GameDatabase,
    rng: random.Random,
    pool: list[int] | None,
    kills_kept: int,
    boss_id: int | None = None,
    rec_lv: int = 10,
    boss_only: bool = False,
) -> list[tuple[int, str]]:
    if not db.mcards_cat:
        return []
    mc = npc.setdefault("mcards", {"own": {}, "sock": {}})
    own: dict[str, list[int]] = mc.setdefault("own", {})
    gained: list[tuple[int, str]] = []

    if pool and kills_kept > 0:
        pool_mc = [db.mcard_by_id[m_id] for m_id in pool if m_id in db.mcard_by_id]
        if pool_mc:
            rolls = max(1, min(14, int(round(kills_kept / 55.0))))
            for _ in range(rolls):
                if rng.random() < db.mcard_drop_mob:
                    c = rng.choice(pool_mc)
                    mk = str(c["id"])
                    arr = own.setdefault(mk, [0, 0, 0])
                    arr[0] = int(arr[0]) + 1
                    gained.append((c["id"], c["n"]))

    if boss_only or boss_id:
        if rng.random() < db.mcard_drop_boss:
            c = db.mcard_by_id.get(boss_id) if boss_id else None
            if c is None:
                near = [x for x in db.mcards_cat if abs(x["lv"] - rec_lv) <= 14] or db.mcards_cat
                c = rng.choice(near)
            mk = str(c["id"])
            arr = own.setdefault(mk, [0, 0, 0])
            arr[0] = int(arr[0]) + 1
            gained.append((c["id"], c["n"]))

    return gained


def process_npc_mcards(
    npc: dict[str, Any],
    db: GameDatabase,
    rng: random.Random,
    ticker: list[str],
    new_events: list[str],
    allow_ticker: bool = True,
) -> None:
    if not db.mcards_cat:
        return
    mc = npc.setdefault("mcards", {"own": {}, "sock": {}})
    own: dict[str, list[int]] = mc.setdefault("own", {})
    sock: dict[str, list[dict[str, int]]] = mc.setdefault("sock", {})
    eq = npc.get("eq") or {}
    cls = npc["cls"]
    lv = npc["lv"]
    tier = npc["tier"]

    # 清理已不在身上的部位插槽
    for sl in list(sock.keys()):
        if sl not in eq or sl not in db.msock_slots or not sock[sl]:
            sock.pop(sl, None)

    # 1) 合成升星（2 普→1 銀 55%；2 銀→1 金 30%，失敗材料破裂）
    max_fuses = 6 if tier == "whale" else 4 if tier == "regular" else 3 if tier == "scalper" else 2
    fuse_count = 0
    cand_mids = list(own.keys())
    rng.shuffle(cand_mids)
    r12 = db.mcard_fuse_rate.get(1, 0.55)
    r23 = db.mcard_fuse_rate.get(2, 0.30)

    for mk in cand_mids:
        if fuse_count >= max_fuses:
            break
        arr = own.get(mk)
        if not isinstance(arr, list) or len(arr) < 3:
            continue
        # 2★ -> 3★
        while arr[1] >= 2 and fuse_count < max_fuses:
            if tier == "casual" and rng.random() > 0.55:
                break
            arr[1] -= 2
            fuse_count += 1
            if rng.random() < r23:
                arr[2] += 1
                info = db.mcard_by_id.get(int(mk))
                if info:
                    new_events.append(f"合成出 3★ 怪物金卡【{info['n']}】！")
                    if allow_ticker and rng.random() < 0.45:
                        ticker.append(f"🃏【怪物金卡】「{npc['name']}」合成出 3★ 金卡【{info['n']}】！")
        # 1★ -> 2★
        while arr[0] >= 2 and fuse_count < max_fuses:
            if tier == "casual" and rng.random() > 0.65:
                break
            arr[0] -= 2
            fuse_count += 1
            if rng.random() < r12:
                arr[1] += 1

    # 2) 建立背包可用卡片清單 [(score, star, mid)]
    avail: list[tuple[float, int, int]] = []
    for mk, arr in own.items():
        if not isinstance(arr, list) or len(arr) < 3:
            continue
        mid = int(mk)
        info = db.mcard_by_id.get(mid)
        if not info:
            continue
        for star in (3, 2, 1):
            cnt = int(arr[star - 1])
            sc = _mcard_score(cls, info["el"], star, lv)
            for _ in range(cnt):
                avail.append((sc, star, mid))
    avail.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # 3) 優先填滿身上裝備空插槽（插卡免費）
    for slot, lim in db.msock_slots.items():
        if slot not in eq:
            continue
        cur_sk = sock.setdefault(slot, [])
        while len(cur_sk) < lim and avail:
            _, star, mid = avail.pop(0)
            arr = own.get(str(mid))
            if arr and arr[star - 1] > 0:
                arr[star - 1] -= 1
                cur_sk.append({"m": mid, "s": star})

    # 4) 有更強高星卡時，若資金充裕支付拔卡費把舊卡拔回圖鑑再換上新卡（所有階層皆可升級替換）
    if avail:
        swaps = 0
        max_swaps = 4 if tier == "whale" else 2 if tier in ("regular", "scalper") else 1
        while swaps < max_swaps and avail:
            best_sc, best_s, best_mid = avail[0]
            worst_pos: tuple[float, str, int, int, int] | None = None
            for slot, lim in db.msock_slots.items():
                if slot not in eq:
                    continue
                for idx_c, card in enumerate(sock.get(slot, [])[:lim]):
                    c_mid = int(card.get("m", 0))
                    c_star = int(card.get("s", 1))
                    c_info = db.mcard_by_id.get(c_mid)
                    c_sc = _mcard_score(cls, c_info["el"] if c_info else "none", c_star, lv)
                    if best_s > c_star and best_sc >= c_sc + 60.0:
                        if worst_pos is None or c_sc < worst_pos[0]:
                            worst_pos = (c_sc, slot, idx_c, c_mid, c_star)
            if worst_pos is None:
                break
            _, slot, idx_c, old_mid, old_star = worst_pos
            fee = _mcard_unsock_gold(old_star, db)
            if npc["gold"] < fee * 2:
                break
            npc["gold"] -= fee
            avail.pop(0)
            arr_new = own.get(str(best_mid))
            if not arr_new or arr_new[best_s - 1] <= 0:
                break
            arr_new[best_s - 1] -= 1
            arr_old = own.setdefault(str(old_mid), [0, 0, 0])
            arr_old[old_star - 1] += 1
            sock[slot][idx_c] = {"m": best_mid, "s": best_s}
            swaps += 1

    # 清理數量皆為 0 的圖鑑空項，縮減存檔大小
    for mk in list(own.keys()):
        if sum(own[mk]) <= 0:
            del own[mk]
    for sl in list(sock.keys()):
        if not sock[sl]:
            del sock[sl]


def mcard_expected_price(
    db: GameDatabase, mid: int, star: int, state: dict[str, Any] | None = None
) -> int:
    """估算怪物卡片市價基準（依怪物等級、屬性熱門度與星級合成成本，含流拍阻尼）"""
    info = db.mcard_by_id.get(mid) or {}
    mob_lv = int(info.get("lv", 10))
    el = str(info.get("el", "earth"))
    el_mult = 1.25 if el in ("fire", "none") else 1.10 if el == "wind" else 1.0
    base1 = int((12_000 + mob_lv * 650) * el_mult)
    star_mult = 1.0 if star <= 1 else 4.2 if star == 2 else 28.0
    all_mult = float((((state or {}).get("dailyEvent") or {}).get("mults") or {}).get("all", 1.0))
    press = min(10, int(((state or {}).get("unsoldPress") or {}).get(f"mc_{mid}_{star}", 0)))
    p_mul = max(0.75, 1.0 - press * 0.025)
    return max(500, int(round(base1 * star_mult * all_mult * p_mul / 5.0) * 5))


def _listing_display(db: GameDatabase, lst: dict[str, Any]) -> tuple[str, str]:
    if "mcard" in lst:
        mid = int(lst["mcard"])
        star = int(lst.get("star", 1))
        star_zh = {1: "普卡", 2: "銀卡", 3: "金卡"}.get(star, f"{star}★")
        mc_n = (db.mcard_by_id.get(mid) or {}).get("n", f"#{mid}")
        return f"{star}★{star_zh}·{mc_n}", mc_n
    iid = int(lst["item"])
    ench = int(lst.get("ench", 0))
    nm = db.items.get(iid, {}).get("name", f"#{iid}")
    return (f"+{ench} {nm}" if ench > 0 else nm), nm


def _npc_mcard_wants_upgrade(npc: dict[str, Any], db: GameDatabase, mid: int, star: int) -> bool:
    """判斷 NPC 買下這張怪物卡是否能直接填補空插槽或升級現有插槽"""
    info = db.mcard_by_id.get(mid)
    if not info:
        return False
    eq = npc.get("eq") or {}
    sock = (npc.get("mcards") or {}).get("sock") or {}
    cls = npc["cls"]
    lv = npc["lv"]
    cand_sc = _mcard_score(cls, info["el"], star, lv)

    for slot, lim in db.msock_slots.items():
        if slot not in eq:
            continue
        cur_sk = sock.get(slot) or []
        if len(cur_sk) < lim:
            return True
        for card in cur_sk[:lim]:
            c_star = int(card.get("s", 1))
            c_mid = int(card.get("m", 0))
            c_info = db.mcard_by_id.get(c_mid)
            c_sc = _mcard_score(cls, c_info["el"] if c_info else "none", c_star, lv)
            if star > c_star and cand_sc >= c_sc + 60.0:
                return True
    return False


def ensure_npc_mcards(npc: dict[str, Any], db: GameDatabase, rng: random.Random) -> dict[str, Any]:
    """初始化或為既有高階存檔 NPC 補齊合理的 V082 怪物卡片收集與插卡進度"""
    mc = npc.get("mcards")
    if isinstance(mc, dict) and isinstance(mc.get("own"), dict) and isinstance(mc.get("sock"), dict):
        return mc
    mc = {"own": {}, "sock": {}}
    npc["mcards"] = mc
    if not db.mcards_cat:
        return mc
    lv = int(npc.get("lv", 1))
    tier = npc.get("tier", "regular")
    runs = int(npc.get("runs", 0))
    if lv <= 1 and runs <= 0:
        return mc

    t_mult = 1.55 if tier == "whale" else 1.0 if tier == "regular" else 0.90 if tier == "scalper" else 0.65
    approx_drops = min(90, max(2, int((lv * 0.95 + min(120, runs) * 0.14) * t_mult)))
    elig = [c for c in db.mcards_cat if c["lv"] <= max(12, lv + 8)] or db.mcards_cat
    own = mc["own"]
    for _ in range(approx_drops):
        c = rng.choice(elig)
        mk = str(c["id"])
        arr = own.setdefault(mk, [0, 0, 0])
        arr[0] += 1
    process_npc_mcards(npc, db, rng, [], [], allow_ticker=False)
    return mc


def make_npc_roster(db: GameDatabase, rng: random.Random) -> list[dict[str, Any]]:
    # 7 職業各 10 位，總計 70 位；同時把 15/30/15/10 均勻打散至各職業
    tier_deck: list[str] = []
    for t, c in TIER_COUNTS.items():
        tier_deck.extend([t] * c)
    rng.shuffle(tier_deck)

    used_names: set[str] = set()
    npcs: list[dict[str, Any]] = []
    idx = 1
    elite_left = dict(WHALE_RETIRE_QUOTAS)
    for ci, cls in enumerate(CLASSES):
        for j in range(10):
            tier = tier_deck[ci * 10 + j]
            wr = False
            if elite_left.get(tier, 0) > 0:
                wr = True
                elite_left[tier] -= 1
            npcs.append(create_new_npc(idx, cls, tier, used_names, db, rng, whale_retire=wr))
            idx += 1
    return npcs


def generate_offline_bark(npc: dict[str, Any], db: GameDatabase, rng: random.Random) -> tuple[str, str | None]:
    """
    產生離線範本台詞：
    - 若個人商店有擺攤商品，優先依據最值錢或最新的販售物品產生叫賣台詞 (shout / talk)。
    - 若無擺攤商品，依據近期事件（升級、爆裝、打王、刷寶）產生一般日常台詞。
    """
    cls_zh = CLS_ZH[npc["cls"]]
    tier = npc["tier"]
    shop = npc.get("shop") or []

    if shop and rng.random() < 0.78:
        # 優先挑單價最高或剛上架的前幾名來叫賣
        featured = max(shop, key=lambda x: x["price"])
        disp, _ = _listing_display(db, featured)
        pr = f"{featured['price']:,}"

        if tier == "scalper":
            templates = [
                f"奇岩大盤商！現貨【{disp}】只要 {pr} 金幣，手慢就沒囉～",
                f"精選好貨上架！【{disp}】特價 {pr} 金幣，點我逛逛商店！",
                f"大量收購各類武防捲與藍裝，店裡【{disp}】只賣 {pr}！",
            ]
        elif tier == "casual":
            templates = [
                f"剛打到【{disp}】急換水錢，低於市價 {pr} 金幣求好心人帶走！",
                f"新手便宜賣～【{disp}】{pr} 金幣就賣，幫幫忙買一下吧！",
                f"背包快滿了，【{disp}】隨便賣 {pr} 金幣，歡迎參觀！",
            ]
        elif tier == "whale":
            templates = [
                f"換下來的【{disp}】便宜釋出 {pr} 金幣，要衝等組隊也可以點我！",
                f"清倉大拍賣，【{disp}】只要 {pr} 金幣，拿去幫分身配裝剛好。",
                f"打王順手出的【{disp}】掛在商店 {pr} 金幣，有緣人直接帶走。",
            ]
        else:
            templates = [
                f"擺攤中！今天有【{disp}】售價 {pr} 金幣，點我名字看商店喔！",
                f"佛系出售【{disp}】{pr} 金幣，另外也有多餘的卷軸材料在賣～",
                f"有需要【{disp}】嗎？我這只賣 {pr} 金幣，價格很公道！",
            ]
        shout = rng.choice(templates)
        return shout, shout

    # 沒擺攤或日常談話
    lv = npc["lv"]
    ev = npc.get("lastEvent", "")
    general = [
        f"目前 Lv.{lv} {cls_zh}，有人要一起出團挑戰高階地圖嗎？",
        "奇岩廣場人真多啊，逛一逛看看有沒有便宜的強化卷軸可以撿！",
        "剛剛差點被龍之谷的怪圍死，還好瞬移按得夠快……",
        "存好金幣準備幫武器衝過安定值了，希望不要白忙一場。",
    ]
    if ev and len(ev) > 4:
        general.append(ev)
    return rng.choice(general), None


def npc_combat_profile(npc: dict[str, Any], db: GameDatabase) -> dict[str, float]:
    """拆解 NPC 三維實戰能力：單體輸出(single_dps)、對群覆蓋清怪(aoe_power)、血防坦度(tank_power)"""
    cls = npc["cls"]
    lv = npc["lv"]
    tier = npc["tier"]
    eq = npc.get("eq") or {}
    card_info = npc.get("card") or {}
    c_tier = card_info.get("tier") if card_info.get("tier") in CARD_TIERS else "white"
    c_def = CARD_TIERS[c_tier]

    w_pts = 0.0
    a_pts = 0.0
    aff_off = 0
    aff_def = 0
    w_aoe = 1.15
    for slot, item in eq.items():
        sc = db.item_power_score(cls, item["id"], item.get("ench", 0))
        it = db.items.get(item["id"], {})
        if it.get("kind") == "weapon":
            if sc > 0:
                w_pts += sc
            w_aoe = max(w_aoe, WEAPON_AOE.get(it.get("type", ""), 1.12))
        else:
            if sc > 0:
                a_pts += sc
        ak = item.get("aff")
        if ak in ("atk", "hit", "spd", "crit"):
            aff_off += 1
        elif ak in ("hp", "dr"):
            aff_def += 1

    if cls in ("mage", "ill"):
        w_aoe += 0.65
    elif cls == "elf":
        w_aoe += 0.35

    mc_b = npc_mcard_bonuses(npc, db)
    skill_mult = 1.16 if tier == "whale" else 1.00 if tier == "regular" else 0.96 if tier == "scalper" else 0.88
    # 對群目標數：武器/職業對群 × 變身卡擴散倍率 × 局內升級技能成長
    aoe_cap = w_aoe * c_def["aoe"] * (0.92 + lv * 0.015)

    # 1) 單體輸出（吃等級、武器、攻撃詞綴、變身卡增傷與攻速、V082火屬性傷害%/風屬性命中）
    mc_off_mul = (1.0 + mc_b["dmgpct"] / 100.0) * (1.0 + min(0.18, mc_b["hit"] * 0.008))
    single_dps = (lv * 1.45 + w_pts * 0.65 + a_pts * 0.06 + aff_off * 3.0 + 6.5) * c_def["dmg"] * c_def["aspd"] * skill_mult * mc_off_mul

    # 2) 對群清場產能（單體 × 對群擴散的 0.72 次方平滑，反映從 1 隻到 8 隻的清怪差距）
    aoe_power = single_dps * (min(9.5, max(1.0, aoe_cap)) ** 0.72) * 0.90

    # 3) 血防坦度（防具積分＋基礎HP成長＋變身卡 HP/AC＋V082地HP/水護盾＋吸血/減傷詞綴）
    hp_g, _ = npc_base_hp_mp(cls, lv, npc.get("stats") or CLASS_BASE_STATS[cls])
    tank_power = (lv * 1.15 + a_pts * 0.28 + (hp_g + mc_b["hp"]) * 0.022 + mc_b["shield"] * 0.028 + c_def["hp"] * 0.08 + abs(c_def["ac"]) * 0.70 + aff_def * 3.5 + 7.0) * skill_mult

    return {
        "single_dps": single_dps,
        "aoe_power": aoe_power,
        "tank_power": tank_power,
        "aoe_cap": aoe_cap,
        "card_rank": float(c_def["rank"]),
    }


def npc_combat_power(npc: dict[str, Any], db: GameDatabase) -> float:
    """綜合推圖戰力（供路線/難度挑選與看板摘要用）"""
    p = npc_combat_profile(npc, db)
    return 0.55 * p["aoe_power"] + 0.45 * p["tank_power"]


def stage_power_req(st: dict[str, Any]) -> float:
    """該層地圖的基準通關需求"""
    mid = st["mapId"]
    if st.get("nopool"):
        return 76.0 if mid == 37 else 84.0
    if mid == 4203:
        return 60.0
    return float(st["recLv"]) * 1.25


def eval_floor_combat(
    prof: dict[str, float], st: dict[str, Any], diff: dict[str, Any], run_luck: float
) -> dict[str, float]:
    """
    方案 2 怪潮對決：
    1) NPC 對群輸出 vs 怪物數量×血量倍率 -> 清怪比 clear_ratio（不代表打不動，而是決定漏掉多少怪）
    2) 漏網圍毆怪數量 × 怪物傷害倍率 vs NPC 血防坦度 -> 承傷比 tank_ratio
    """
    rec_lv = float(st["recLv"])
    d_idx = int(diff["idx"])
    mon_k = float(diff["mon"])
    dvhp = max(1.0, float(st.get("dvhp", 1.0)))
    dvdmg = max(1.0, float(st.get("dvdmg", 1.0)))
    base_mobs = float(st.get("baseMobs", 22.0 + rec_lv * 1.1))
    diff_mob_mult = 0.55 if d_idx == 0 else 1.0 if d_idx == 1 else 1.32 if d_idx == 2 else 1.68
    horde_count = min(80.0, base_mobs * diff_mob_mult)

    # 這層怪物群的清場負荷（結合推薦等級、怪物實際血量與 DVHP 樓層硬度、難度倍率）
    # 對齊 combined=1.0 -> survival_prob=58% 的真實比例刻度
    avg_hp = max(40.0, float(st.get("avgMobHp", rec_lv * 8.0)))
    hp_scale = (avg_hp / max(50.0, rec_lv * 7.5)) ** 0.20
    horde_hp_load = (rec_lv * 7.80 + 38.0) * ((horde_count / 28.0) ** 0.44) * (dvhp ** 0.24) * (mon_k ** 0.36) * hp_scale
    clear_ratio = (prof["aoe_power"] * run_luck) / max(15.0, horde_hp_load)

    # 清不完而溢出的怪比例：clear_ratio 越低，越多怪衝到臉上圍毆
    overflow = max(0.22, min(2.05, 0.42 + max(0.0, 1.08 - clear_ratio) * 1.15))
    mob_dmg_load = (rec_lv * 4.25 + 24.0) * dvdmg * (mon_k ** 0.34) * overflow
    tank_ratio = (prof["tank_power"] * (0.85 + 0.30 * run_luck)) / max(15.0, mob_dmg_load)

    combined = 0.48 * clear_ratio + 0.52 * tank_ratio
    surv_p = survival_prob(combined * 10.0, 10.0)
    return {
        "mobs": horde_count,
        "clr": clear_ratio,
        "tank": tank_ratio,
        "overflow": overflow,
        "surv_p": surv_p,
    }


def eval_boss_combat(
    prof: dict[str, float], st: dict[str, Any], diff: dict[str, Any], run_luck: float, is_dragon: bool = False
) -> float:
    """首領／龍王單挑檢定：依賴單體輸出與續戰坦度（擴散不幫打單隻王）"""
    boss_k = float(diff["boss"])
    if is_dragon:
        base_req = stage_power_req(st) * (0.62 if diff["idx"] == 0 else 1.0 if diff["idx"] == 1 else 1.38 if diff["idx"] == 2 else 1.85) * 1.05
    else:
        b_lv = float(st.get("bossLv", st["recLv"] + 5))
        b_hp = float(st.get("bossHp", 12000))
        hp_fac = (b_hp / 12000.0) ** 0.14
        base_req = (b_lv * 1.25 + 6.5) * hp_fac * (boss_k ** 0.36)
    boss_eff = (0.58 * prof["single_dps"] + 0.42 * prof["tank_power"]) * run_luck
    return survival_prob(boss_eff, base_req)


def choose_segment_and_diff(
    npc: dict[str, Any], db: GameDatabase, rng: random.Random, force_hard: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    lv = npc["lv"]
    tier = npc["tier"]
    tier_offset = 3 if tier == "whale" else 1 if tier == "regular" else -1 if tier == "casual" else 0
    target_entry = max(9, lv + tier_offset)
    max_entry = max(9, lv + (3 if tier == "whale" else 2 if tier == "regular" else 1))

    # 新手／一般／商人偶爾會往下挑選低於自己能力的關卡，改刷困難或惡夢拚經驗與掉寶
    farm_low_prob = 0.38 if tier == "casual" else 0.35 if tier == "scalper" else 0.32 if tier == "regular" else 0.0
    low_cands = [g for g in db.segments if g["peakRec"] <= max(10, lv - 2) and g["entryRec"] <= max(9, lv - 4)]
    if lv >= 14 and low_cands and rng.random() < farm_low_prob:
        low_target = max(9, lv - rng.randint(4, 9))
        low_cands.sort(key=lambda g: (abs(g["entryRec"] - low_target), g["peakRec"]))
        seg = rng.choice(low_cands[: min(4, len(low_cands))])
    else:
        # 嚴格依目前等級挑選合理入口路線，禁止 Lv.1~5 直接排進奇岩/海音/龍谷越級賭運氣
        cands = [g for g in db.segments if g["entryRec"] <= max_entry]
        if not cands:
            cands = [db.segments[0]]
        cands.sort(key=lambda g: (abs(g["entryRec"] - target_entry), abs(g["peakRec"] - target_entry)))
        seg = rng.choice(cands[: min(3, len(cands))])

    # 以實際路線中段層在 普通 / 困難 / 惡夢 的清怪與坦度比決定難度，確保刷高難度是挑實力能過關的低等區
    prof = npc_combat_profile(npc, db)
    ref_stage = seg["stages"][len(seg["stages"]) // 2]
    norm_diff = db.diffs[1] if len(db.diffs) > 1 else db.diffs[0]
    hard_diff = db.diffs[2] if len(db.diffs) > 2 else norm_diff
    nm_diff = db.diffs[3] if len(db.diffs) > 3 else hard_diff

    def _rel(d_obj: dict[str, Any]) -> float:
        ev = eval_floor_combat(prof, ref_stage, d_obj, 1.0)
        return 0.48 * ev["clr"] + 0.52 * ev["tank"]

    rel = _rel(norm_diff)
    rel_hard = _rel(hard_diff)
    rel_nm = _rel(nm_diff)

    if tier == "whale":
        if rel < 0.72:
            weights = [0.78, 0.22, 0.0, 0.0]
        elif rel < 1.05:
            weights = [0.25, 0.58, 0.17, 0.0]
        elif rel < 1.40:
            weights = [0.05, 0.35, 0.48, 0.12]
        else:
            weights = [0.0, 0.18, 0.48, 0.34]
    elif tier == "casual":
        if rel_nm >= 0.92:
            weights = [0.06, 0.18, 0.38, 0.38]
        elif rel_hard >= 0.88:
            weights = [0.16, 0.36, 0.38, 0.10 if rel_nm >= 0.75 else 0.0]
        elif rel < 0.72:
            weights = [0.78, 0.22, 0.0, 0.0]
        else:
            weights = [0.60, 0.34, 0.06, 0.0] if rel < 1.25 else [0.32, 0.52, 0.16, 0.0]
    elif tier == "scalper":
        if rel_nm >= 0.92:
            weights = [0.0, 0.16, 0.40, 0.44]
        elif rel_hard >= 0.88:
            weights = [0.05, 0.38, 0.45, 0.12 if rel_nm >= 0.75 else 0.0]
        elif rel < 0.72:
            weights = [0.75, 0.25, 0.0, 0.0]
        else:
            weights = [0.35, 0.60, 0.05, 0.0]
    else:  # regular
        if rel_nm >= 0.92:
            weights = [0.0, 0.14, 0.40, 0.46]
        elif rel_hard >= 0.88:
            weights = [0.05, 0.35, 0.46, 0.14 if rel_nm >= 0.75 else 0.0]
        elif rel < 0.72:
            weights = [0.72, 0.28, 0.0, 0.0]
        elif rel < 1.05:
            weights = [0.42, 0.53, 0.05, 0.0]
        else:
            weights = [0.12, 0.58, 0.28, 0.02]

    if force_hard and tier in ("whale", "regular") and rel >= 0.85:
        weights = [0.0, 0.25 * weights[0], weights[1] * 0.6 + weights[2], weights[3] + weights[1] * 0.4 + weights[0] * 0.75]
    diff = rng.choices(db.diffs, weights=weights, k=1)[0]
    return seg, diff


def survival_prob(eff_power: float, req: float) -> float:
    """硬核真實通關機率曲線：實力相當約 58%，嚴重越級或戰力不足低至 0.5%"""
    ratio = eff_power / max(6.0, req)
    z = 4.6 * (ratio - 1.0) + 0.32
    return max(0.005, min(0.97, 1.0 / (1.0 + math.exp(-z))))


def simulate_tick(
    state: dict[str, Any], db: GameDatabase, rng: random.Random, args: argparse.Namespace, allow_live_event: bool = False
) -> dict[str, Any]:
    state["tick"] = state.get("tick", 0) + 1
    tick_id = state["tick"]
    npcs: list[dict[str, Any]] = state["npcs"]
    market: dict[str, int] = state.setdefault("market", {})
    ticker: list[str] = state.setdefault("ticker", [])
    retired_total = state.get("retiredCount", 0)
    retired_by_tier = state.setdefault("retiredByTier", {t: 0 for t in TIER_COUNTS})

    # --- 0. 每日市場事件卡（每 24 ticks 換 1 張：20 張主軸卡）；台美股與台北天氣於即時模式每次皆重抓 ---
    cur_ev = state.get("dailyEvent")
    if not cur_ev or tick_id > int(cur_ev.get("untilTick", 0)):
        ev_new = roll_daily_event(state, rng, tick_id, allow_live=allow_live_event)
        ticker.append(f"{ev_new['title']} {ev_new['desc']}（{ev_new['source']}）")
    elif allow_live_event:
        refresh_daily_event_signals(state)

    # --- 0b. 奇岩商人每 3~5 天鉅額虧損事件（不破產）---
    apply_merchant_loss_event(npcs, state, rng, tick_id, ticker)

    # 確保所有 70 人都有合理市場基準
    used_names = {n["name"] for n in npcs}

    # --- 1. 推進每名 NPC 1 小時打怪／升級／掉寶／消耗 ---
    next_listing_seq = state.setdefault("nextSeq", 1000)

    retire_lv = max(10, min(50, int(getattr(args, "retire_lv", 50))))
    for npc in npcs:
        # 清理若從舊資料集升級時已移除的道具編號，確保改版不相容不崩潰
        for sl in list(npc.get("eq", {}).keys()):
            if npc["eq"][sl].get("id") not in db.items:
                del npc["eq"][sl]
        npc["ageTicks"] = npc.get("ageTicks", 0) + 1
        tier = npc["tier"]
        cls = npc["cls"]
        r_min, r_max = TIER_RUNS_PER_DAY[tier]
        expected_runs = rng.uniform(r_min, r_max) / 24.0
        n_runs = int(expected_runs) + (1 if rng.random() < (expected_runs % 1.0) else 0)
        if n_runs == 0 and npc["ageTicks"] % 8 == 0:
            n_runs = 1

        # 記錄最近發生的精彩事蹟（每「場」= 從起點走完一條完整 SEGMENTS 流程關卡，中途輸了就結束該場）
        new_events: list[str] = []
        if not isinstance(npc.get("card"), dict) or npc["card"].get("tier") not in CARD_TIERS:
            npc["card"] = init_npc_card(tier, npc["lv"], rng)
        ensure_npc_mcards(npc, db, rng)
        mc_exp_mul = 1.0 + npc_mcard_bonuses(npc, db).get("exppct", 0.0) / 100.0
        clog: list[dict[str, Any]] = npc.setdefault("combatLog", [])
        prof = npc_combat_profile(npc, db)
        force_hard_ev = bool((state.get("dailyEvent") or {}).get("forceHard", False))
        for _ in range(n_runs):
            npc["runs"] = npc.get("runs", 0) + 1
            seg, diff = choose_segment_and_diff(npc, db, rng, force_hard=force_hard_ev)
            # 本場局內抽卡／走位操作手氣
            run_luck = rng.uniform(0.76, 1.32)

            floors = seg["stages"]
            if tier == "whale":
                power_factor = min(2.35, 0.72 + npc["lv"] * 0.038)
            elif tier == "regular":
                power_factor = min(1.65, 0.52 + npc["lv"] * 0.025)
            elif tier == "scalper":
                power_factor = min(1.35, 0.45 + npc["lv"] * 0.019)
            else:
                power_factor = min(1.05, 0.32 + npc["lv"] * 0.014)

            # 單場多關卡時，每層的清怪密度縮放，避免多層圖經驗與道具過度暴衝
            floor_scale = 0.62 if len(floors) >= 3 else 0.78 if len(floors) == 2 else 1.0
            diff_gold_mult = (2.1 if tier == "whale" else 1.9 if tier == "scalper" else 1.5 if tier == "regular" else 1.1) * (
                0.75 if diff["idx"] == 0 else 1.0 if diff["idx"] == 1 else 1.35 if diff["idx"] == 2 else 1.85
            )

            run_gold_acc = 0
            run_exp_acc = 0
            run_kills_acc = 0
            run_floors: list[dict[str, Any]] = []
            run_drops: dict[tuple[int, int], int] = {}
            stop_reason: str | None = None
            wiped = False

            def _rec_drop(iid_d: int, ench_d: int, qty_d: int) -> None:
                dk_d = (iid_d, ench_d)
                run_drops[dk_d] = run_drops.get(dk_d, 0) + qty_d

            for st in floors:
                pool = st["pool"]
                upkeep_fl = 850 + st["recLv"] * (210 if tier == "whale" else 135 if tier == "regular" else 70)

                if not st.get("nopool") and pool:
                    # 1) 小怪推進與掉落預骰（擊殺數與經驗連動實際清怪比，清不動就不會虛高暴升）
                    fl_res = eval_floor_combat(prof, st, diff, run_luck)
                    surv_p = fl_res["surv_p"]
                    kill_eff = max(0.15, min(1.05, fl_res["clr"] * 0.90))
                    kills = int(rng.randint(850, 1550) * power_factor * floor_scale * kill_eff)
                    sample_mons = [db.monsters_by_id[rng.choice(pool)] for _ in range(10)]
                    avg_mexp = sum(db.mexp.get(int(m["id"]), 2) for m in sample_mons) / max(1, len(sample_mons))
                    exp_gain = int(kills * avg_mexp * 0.78 * diff["exp"] * mc_exp_mul)

                    drop_rolls = max(4, min(16, int((7 + npc["lv"] // 4) * floor_scale * max(0.45, kill_eff))))
                    fl_gold = int((rng.randint(3200, 7800) + st["recLv"] * rng.randint(360, 720)) * diff_gold_mult * floor_scale * max(0.35, kill_eff))
                    rolled_loot: list[tuple[int, int, int, dict[str, str] | None]] = []
                    for _r in range(drop_rolls):
                        mon = db.monsters_by_id[rng.choice(pool)]
                        for dr in mon.get("drops", []):
                            iid = int(dr["item"])
                            if iid == GOLD_ID:
                                fl_gold += int(rng.randint(int(dr.get("min", 25)), int(dr.get("max", 90))) * 3.5 * diff_gold_mult * floor_scale)
                                continue
                            if iid in db.no_drop or iid not in db.items:
                                continue
                            it = db.items[iid]
                            drop_mult = diff["rare"] if it.get("kind") in ("weapon", "armor") else diff["drop"]
                            ch = (int(dr.get("chance", 10000)) * db.matk.get(iid, 1.0) * drop_mult) / 1_000_000.0
                            eff_roll = min(0.28, ch * 2.4)
                            if rng.random() < eff_roll:
                                ench = 0
                                aff = None
                                if it.get("kind") in ("weapon", "armor"):
                                    if rng.random() < 0.08:
                                        ench = 1
                                    if st["recLv"] >= 20 and rng.random() < 0.08:
                                        aff = rng.choice(AFFIX_POOL)
                                    rolled_loot.append((iid, ench, 1, aff))
                                else:
                                    qty = rng.randint(int(dr.get("min", 1)), int(dr.get("max", 1)))
                                    rolled_loot.append((iid, 0, qty, None))

                    # 小怪層撐不撐得過檢定（沒撐過就半途翻車：擊殺／經驗／金幣／掉落嚴格懲罰）
                    if rng.random() > surv_p:
                        kills_kept = int(kills * 0.25)
                        npc["kills"] = npc.get("kills", 0) + kills_kept
                        run_kills_acc += kills_kept
                        roll_npc_mcard_drops(npc, db, rng, pool, kills_kept, rec_lv=st["recLv"])
                        exp_gain = int(exp_gain * 0.30)
                        run_exp_acc += exp_gain
                        fl_gold_net = max(0, int(fl_gold * 0.20) - upkeep_fl)
                        run_gold_acc += fl_gold_net
                        for iid_l, e_l, q_l, aff_l in rolled_loot:
                            if rng.random() < 0.20:
                                self_bag_add(npc, iid_l, e_l, q_l, aff_l)
                                _rec_drop(iid_l, e_l, q_l)
                        wiped = True
                        stop_reason = f"{st['name']}·怪潮"
                        run_floors.append({
                            "m": st["name"], "n": round(fl_res["mobs"]), "clr": round(fl_res["clr"], 2),
                            "tk": round(fl_res["tank"], 2), "p": round(surv_p, 2), "ok": False, "bp": None, "bok": None,
                        })
                        if diff["idx"] >= 2 or st["recLv"] >= 30:
                            new_events.append(f"在{diff['name']}【{st['name']}】不敵怪潮飲恨撤退")
                        if npc["lv"] < retire_lv:
                            npc["exp"] += exp_gain
                            while npc["lv"] < retire_lv and npc["exp"] >= (db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]):
                                npc["exp"] -= db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]
                                npc["lv"] += 1
                        break

                    # 小怪層過關：全額領取當層小怪掉落
                    npc["kills"] = npc.get("kills", 0) + kills
                    run_kills_acc += kills
                    roll_npc_mcard_drops(npc, db, rng, pool, kills, rec_lv=st["recLv"])
                    for iid_l, e_l, q_l, aff_l in rolled_loot:
                        self_bag_add(npc, iid_l, e_l, q_l, aff_l)
                        _rec_drop(iid_l, e_l, q_l)

                    # 2) 本層首領判定（若有）
                    boss_ok: bool | None = None
                    boss_p_val: float | None = None
                    if st["bossId"]:
                        boss_p = eval_boss_combat(prof, st, diff, run_luck, is_dragon=False)
                        boss_p_val = round(boss_p, 2)
                        if rng.random() < boss_p:
                            boss_ok = True
                            bmon = db.monsters_by_id[st["bossId"]]
                            exp_gain += int(db.bxp.get(st["mapId"], 110) * diff["exp"] * mc_exp_mul)
                            mc_boss_got = roll_npc_mcard_drops(npc, db, rng, None, 0, boss_id=st["bossId"], rec_lv=st["recLv"])
                            for _, mc_nm in mc_boss_got:
                                new_events.append(f"推倒{bmon['name']}掉落怪物卡【{mc_nm}】！")
                            fl_gold += int(rng.randint(12000, 36000) * diff_gold_mult)
                            if rng.random() < min(0.85, 0.38 * diff["bossrw"]) and db.chest_mats:
                                mat_id = rng.choice(db.chest_mats)
                                if mat_id in db.items:
                                    self_bag_add(npc, mat_id, 0, 1, None)
                                    _rec_drop(mat_id, 0, 1)
                            if rng.random() < 0.014 * diff["bossrw"]:
                                self_bag_add(npc, 84072, 0, 1, None)
                                _rec_drop(84072, 0, 1)
                            for dr in bmon.get("drops", []):
                                iid = int(dr["item"])
                                if iid == GOLD_ID or iid in db.no_drop or iid not in db.items:
                                    continue
                                ch = (int(dr.get("chance", 10000)) * db.matk.get(iid, 1.0) * diff["bossrw"]) / 1_000_000.0
                                if rng.random() < min(0.45, ch):
                                    it = db.items[iid]
                                    aff = rng.choice(AFFIX_POOL) if (it.get("kind") in ("weapon", "armor") and rng.random() < 0.18) else None
                                    self_bag_add(npc, iid, 0, 1, aff)
                                    _rec_drop(iid, 0, 1)
                                    if db.market_base.get(iid, 0) >= 250_000:
                                        ticker.append(f"💎【打王掉寶】「{npc['name']}」推倒{diff['name']}{bmon['name']}喜獲【{it['name']}】！")
                        else:
                            # 在該層尾王倒下：小怪成果保留，但該層金幣打折、結束這趟流程
                            boss_ok = False
                            fl_gold_net = max(0, int(fl_gold * 0.75) - upkeep_fl)
                            run_gold_acc += fl_gold_net
                            run_exp_acc += exp_gain
                            wiped = True
                            stop_reason = f"{st['name']}·首領"
                            run_floors.append({
                                "m": st["name"], "n": round(fl_res["mobs"]), "clr": round(fl_res["clr"], 2),
                                "tk": round(fl_res["tank"], 2), "p": round(surv_p, 2), "ok": True, "bp": boss_p_val, "bok": False,
                            })
                            if diff["idx"] >= 2 or st["recLv"] >= 28:
                                new_events.append(f"挑戰{diff['name']}【{st['name']}】首領失敗")
                            if npc["lv"] < retire_lv:
                                npc["exp"] += exp_gain
                                while npc["lv"] < retire_lv and npc["exp"] >= (db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]):
                                    npc["exp"] -= db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]
                                    npc["lv"] += 1
                            break

                    run_floors.append({
                        "m": st["name"], "n": round(fl_res["mobs"]), "clr": round(fl_res["clr"], 2),
                        "tk": round(fl_res["tank"], 2), "p": round(surv_p, 2), "ok": True, "bp": boss_p_val, "bok": boss_ok,
                    })
                    run_gold_acc += fl_gold - upkeep_fl
                    run_exp_acc += exp_gain
                    if npc["lv"] < retire_lv:
                        npc["exp"] += exp_gain
                        while npc["lv"] < retire_lv and npc["exp"] >= (db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]):
                            npc["exp"] -= db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]
                            npc["lv"] += 1
                            new_events.append(f"剛升上 Lv.{npc['lv']}！離頂點又近了一步")
                            if npc["lv"] >= retire_lv:
                                npc["exp"] = 0
                                ticker.append(f"🎉【滿級賀電】{CLS_ZH[cls]}「{npc['name']}」在{st['name']}達成 Lv.{retire_lv}！")
                else:
                    # 3) 純單王終局房（安塔瑞斯 37 / 法利昂 65）
                    bmon = db.monsters_by_id.get(st["bossId"]) if st["bossId"] else None
                    if not bmon:
                        continue
                    dragon_p = eval_boss_combat(prof, st, diff, run_luck, is_dragon=True)
                    if rng.random() < dragon_p:
                        npc["kills"] = npc.get("kills", 0) + 1
                        run_kills_acc += 1
                        exp_gain = int(db.bxp.get(st["mapId"], 500) * diff["exp"] * mc_exp_mul)
                        mc_drg_got = roll_npc_mcard_drops(npc, db, rng, None, 0, boss_id=st["bossId"], rec_lv=st["recLv"], boss_only=True)
                        for _, mc_nm in mc_drg_got:
                            new_events.append(f"斬殺{bmon['name']}取得怪物卡【{mc_nm}】！")
                        run_exp_acc += exp_gain
                        fl_gold = int(rng.randint(60000, 140000) * diff_gold_mult)
                        run_gold_acc += fl_gold - upkeep_fl
                        if rng.random() < min(0.95, 0.65 * diff["bossrw"]) and db.chest_mats:
                            for _c in range(2):
                                mat_id = rng.choice(db.chest_mats)
                                if mat_id in db.items:
                                    self_bag_add(npc, mat_id, 0, 1, None)
                                    _rec_drop(mat_id, 0, 1)
                        if rng.random() < 0.04 * diff["bossrw"]:
                            self_bag_add(npc, 84072, 0, 1, None)
                            _rec_drop(84072, 0, 1)
                        for dr in bmon.get("drops", []):
                            iid = int(dr["item"])
                            if iid == GOLD_ID or iid in db.no_drop or iid not in db.items:
                                continue
                            ch = (int(dr.get("chance", 10000)) * db.matk.get(iid, 1.0) * diff["bossrw"]) / 1_000_000.0
                            if rng.random() < min(0.55, ch):
                                it = db.items[iid]
                                aff = rng.choice(AFFIX_POOL) if (it.get("kind") in ("weapon", "armor") and rng.random() < 0.25) else None
                                self_bag_add(npc, iid, 0, 1, aff)
                                _rec_drop(iid, 0, 1)
                        run_floors.append({
                            "m": st["name"], "n": 1, "clr": None, "tk": None,
                            "p": None, "ok": True, "bp": round(dragon_p, 2), "bok": True,
                        })
                        ticker.append(f"🐉【屠龍捷報】「{npc['name']}」在{diff['name']}【{st['name']}】成功斬殺【{bmon['name']}】！")
                        new_events.append(f"破關{diff['name']}「{seg['label']}」並擊敗{bmon['name']}！")
                        if npc["lv"] < retire_lv:
                            npc["exp"] += exp_gain
                            while npc["lv"] < retire_lv and npc["exp"] >= (db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]):
                                npc["exp"] -= db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]
                                npc["lv"] += 1
                                if npc["lv"] >= retire_lv:
                                    npc["exp"] = 0
                                    ticker.append(f"🎉【滿級賀電】{CLS_ZH[cls]}「{npc['name']}」屠龍後達成 Lv.{retire_lv}！")
                    else:
                        run_gold_acc -= upkeep_fl
                        wiped = True
                        stop_reason = f"{st['name']}·{bmon['name']}"
                        run_floors.append({
                            "m": st["name"], "n": 1, "clr": None, "tk": None,
                            "p": None, "bp": round(dragon_p, 2), "bok": False,
                        })
                        ticker.append(f"💀【挑戰失利】「{npc['name']}」勇闖{diff['name']}【{st['name']}】遭{bmon['name']}擊退！")
                        new_events.append(f"於{diff['name']}【{st['name']}】遭{bmon['name']}擊退")
                        break

            npc["gold"] = max(0, min(TIER_GOLD_CAP[tier], npc["gold"] + run_gold_acc))
            # 紀錄本場戰鬥資訊至後台 npc_state.json（滾動保留最近 66 ticks）
            sorted_drops = sorted(
                run_drops.items(),
                key=lambda kv: db.market_base.get(kv[0][0], 0) * kv[1],
                reverse=True,
            )[:12]
            clog.append({
                "t": tick_id,
                "lv": npc["lv"],
                "seg": seg["label"],
                "diff": diff["name"],
                "card": npc["card"]["zh"],
                "aoe": round(prof["aoe_cap"], 2),
                "luck": round(run_luck, 2),
                "win": not wiped,
                "stop": stop_reason,
                "k": run_kills_acc,
                "exp": run_exp_acc,
                "g": run_gold_acc,
                "fl": run_floors,
                "dr": [
                    {"id": iid_r, "nm": db.items.get(iid_r, {}).get("name", f"#{iid_r}"), "e": e_r, "n": q_r}
                    for (iid_r, e_r), q_r in sorted_drops
                ],
            })
            # 過關或打完一場有機會進階變身圖鑑卡，並即時重算戰鬥體質
            old_ct = npc["card"]["tier"]
            maybe_upgrade_npc_card(npc, rng, boost=(1.45 if not wiped else 0.65))
            if npc["card"]["tier"] != old_ct:
                prof = npc_combat_profile(npc, db)
                new_events.append(f"抽到新變身卡【{npc['card']['zh']}·{npc['card']['name']}】！")

        # 滾動裁切戰鬥紀錄，只保留最近 66 ticks
        cutoff_tick = tick_id - 66
        if clog:
            npc["combatLog"] = [r for r in clog if r.get("t", 0) > cutoff_tick]

        # 高金幣 NPC 超過開箱門檻開始開曼孟裝備箱；奇岩商人達 5,000 萬起有 20% 機率小額開 1 箱賭安定+2以上，超過 1.6 億才啟動高頻抽水
        sink_cap = TIER_SINK_CAP[tier]
        box_spend = 0
        if tier == "scalper":
            if npc["gold"] > sink_cap:
                box_spend = min(npc["gold"] - sink_cap // 2, 3_000_000 if rng.random() < 0.45 else 1_000_000)
            elif npc["gold"] >= 50_000_000 and rng.random() < 0.20:
                box_spend = 1_000_000
        elif npc["gold"] > sink_cap:
            box_spend = min(npc["gold"] - sink_cap // 2, 3_000_000 if rng.random() < 0.35 else 1_000_000)

        if box_spend > 0:
            spend = box_spend
            npc["gold"] -= spend
            if spend >= 1_000_000 and db.box_pool:
                bi = rng.choice(db.box_pool)
                be = 4 if rng.random() < 0.05 else 3 if rng.random() < 0.15 else 2 if rng.random() < 0.30 else 0
                if bi in db.items:
                    if tier == "scalper":
                        it_b = db.items[bi]
                        b_kind = it_b.get("kind", "armor")
                        b_safe = max(0, int(it_b.get("safe", 6 if b_kind == "weapon" else 4)))
                        scr_id = SCROLL_WEAPON_NORMAL if b_kind == "weapon" else SCROLL_ARMOR_NORMAL
                        scr_cost = db.shop_anchor.get(scr_id, 85_000 if b_kind == "weapon" else 41_000)
                        min_target = b_safe + 2
                        cur_e = be
                        exploded = False
                        while True:
                            if cur_e >= min_target:
                                push_prob = 0.45 * (0.68 ** (cur_e - min_target))
                                if rng.random() >= push_prob:
                                    break
                            if npc["gold"] < scr_cost:
                                break
                            npc["gold"] -= scr_cost
                            p_step = _npc_enchant_step_prob(b_kind, cur_e, b_safe, blessed=False, uncapped=True)
                            if rng.random() < p_step:
                                cur_e += 1
                            elif cur_e >= b_safe:
                                exploded = True
                                break
                        if not exploded:
                            self_bag_add(npc, bi, cur_e, 1, None)
                            if cur_e >= min_target:
                                ticker.append(f"💎【商會豪賭】「{npc['name']}」開箱買捲衝出【+{cur_e} {it_b['name']}】！")
                        elif cur_e >= min_target:
                            ticker.append(f"💥【豪賭成灰】「{npc['name']}」衝 +{cur_e+1} {it_b['name']} 失敗蒸發……")
                    else:
                        self_bag_add(npc, bi, be, 1, None)
                maybe_upgrade_npc_card(npc, rng, boost=2.6)
        npc["gold"] = max(0, min(TIER_GOLD_CAP[tier], npc["gold"]))

        # --- 2. 利用 craftdb.js 提煉中間素材或打造製作裝備/餅乾 ---
        craft_npc_items(npc, db, market, rng, ticker, new_events)

        # --- 3. 自動換裝＋強化消耗（裝備／卷軸黑洞）---
        ench_bonus = int((state.get("dailyEvent") or {}).get("enchantBonus", 0))
        upgrade_npc_gear_and_enchant(npc, db, market, rng, ticker, new_events, ench_bonus=ench_bonus)

        # --- 3a. V082 怪物卡片合成升星＋裝備自動插卡／換高星卡 ---
        process_npc_mcards(npc, db, rng, ticker, new_events)

        # --- 3b. 奇岩商人偶爾拿背包多餘藍／白裝或安定 0 飾品賭強化轉賣 ---
        scalper_gamble_resell_gear(npc, db, market, rng, ticker)

        # --- 3c. 整理背包 → 下架過期品／補進個人商店貨架 ---
        restock_npc_shop(npc, db, market, rng, tick_id, state)

        if new_events:
            npc["lastEvent"] = new_events[-1]

    # --- 4. NPC ↔ NPC 跨商店市場交易（10% 稅金銷毀，減稅日降為 3%）---
    run_npc_market_clearing(npcs, db, market, rng, ticker, state)

    # --- 5. 滿級（預設 Lv.50）退坑循環 → 補進 Lv.1 新手 ---
    for i, npc in enumerate(npcs):
        if npc["lv"] >= retire_lv:
            npc["maxLvTicks"] = npc.get("maxLvTicks", 0) + 1
        # 滿級與非肝帝分級／高齡自然退坑循環（肝帝與 9 位元老角不吃年齡與 35~45 等退坑，僅滿級後 7% 退坑；其餘高齡為 1000 ticks）
        is_whale_retire = (npc["tier"] == "whale") or bool(npc.get("whaleRetire"))
        retire_prob = 0.0
        if npc["maxLvTicks"] >= 18:
            retire_prob = 0.07 if is_whale_retire else 0.05
        if not is_whale_retire:
            if npc["lv"] > 45:
                retire_prob = max(retire_prob, 0.02)
            elif npc["lv"] > 40:
                retire_prob = max(retire_prob, 0.0125)
            elif npc["lv"] > 35:
                retire_prob = max(retire_prob, 0.01)
            if npc.get("ageTicks", 0) > 1000:
                retire_prob = max(retire_prob, 0.012)
        if retire_prob > 0 and rng.random() < retire_prob:
            retired_total += 1
            old_name = npc["name"]
            old_cls = npc["cls"]
            old_tier = npc["tier"]
            retired_by_tier[old_tier] = retired_by_tier.get(old_tier, 0) + 1
            old_lv = npc["lv"]
            used_names.discard(old_name)
            fresh = create_new_npc(
                npc["id"], old_cls, old_tier, used_names, db, rng, whale_retire=bool(npc.get("whaleRetire"))
            )
            npcs[i] = fresh
            ticker.append(
                f"🌟【傳承交棒】Lv.{old_lv} {CLS_ZH[old_cls]}「{old_name}」功成身退！新人「{fresh['name']}」加入冒險！"
            )

    state["retiredCount"] = retired_total

    # --- 6. 動態調整物價市場基準 + 全服通膨檢查 ---
    update_market_prices(npcs, db, market)

    # --- 7. 指派在線狀態（約 30~38 人在村莊逛街/擺攤，其餘 32~40 人在打寶）＆ 產生離線叫賣台詞 ---
    for npc in npcs:
        shop_bias = 0.56 if npc.get("shop") else 0.32
        npc["state"] = "village" if rng.random() < shop_bias else "battle"

    vil_npcs = [n for n in npcs if n["state"] == "village"]
    if len(vil_npcs) < 28:
        bat = [n for n in npcs if n["state"] == "battle"]
        rng.shuffle(bat)
        for n in bat[: 28 - len(vil_npcs)]:
            n["state"] = "village"
    elif len(vil_npcs) > 38:
        rng.shuffle(vil_npcs)
        for n in vil_npcs[38:]:
            n["state"] = "battle"

    for npc in npcs:
        talk, shout = generate_offline_bark(npc, db, rng)
        npc["talk"] = talk
        npc["shout"] = shout

    if len(ticker) > 40:
        state["ticker"] = ticker[-40:]

    return state


def self_bag_add(npc: dict[str, Any], iid: int, ench: int, qty: int, aff: dict[str, str] | None) -> None:
    aff_k = aff["k"] if aff else ""
    key = f"{iid}:{ench}:{aff_k}"
    bag = npc["bag"]
    bag[key] = bag.get(key, 0) + qty


def craft_npc_items(
    npc: dict[str, Any],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    ticker: list[str],
    new_events: list[str],
) -> None:
    """依 CRAFTDB 配方讓 NPC 把收集到的材料提煉成中階材料或成品裝備"""
    if not db.recipes:
        return
    bag = npc["bag"]

    def bag_cnt(iid: int) -> int:
        return bag.get(f"{iid}:0:", 0)

    def bag_take(iid: int, qty: int) -> None:
        k = f"{iid}:0:"
        if k in bag:
            bag[k] -= qty
            if bag[k] <= 0:
                del bag[k]

    # 先有機率把基礎素材提煉成線/網/蛻皮/樹皮/奧里哈魯根/金屬板等中間料（留一部份原料給玩家買）
    intermediate_recipes = [r for r in db.recipes if db.items.get(r["out"], {}).get("kind") == "etc" and r["out"] != 40378]
    rng.shuffle(intermediate_recipes)
    crafted_mid = 0
    for r in intermediate_recipes:
        if crafted_mid >= 2:
            break
        # 只保留一定機率提煉，讓原料與半成品同時會在市面上流動
        if rng.random() > 0.35:
            continue
        if npc["gold"] < r["gold"] or any(bag_cnt(in_id) < cnt for in_id, cnt in r["inputs"]):
            continue
        for in_id, cnt in r["inputs"]:
            bag_take(in_id, cnt)
        if r["gold"] > 0:
            npc["gold"] -= r["gold"]
        self_bag_add(npc, r["out"], 0, r["yield"], None)
        crafted_mid += 1

    # 再檢視是否能合成高價值製作裝 (如力量手套 / 細劍 / 精靈防具 / 法丘 等)
    equip_recipes = [r for r in db.recipes if db.items.get(r["out"], {}).get("kind") in ("weapon", "armor")]
    rng.shuffle(equip_recipes)
    for r in equip_recipes:
        gold_need = r["gold"] + r["coin"] * 60000
        if npc["gold"] < gold_need:
            continue
        if any(bag_cnt(in_id) < cnt for in_id, cnt in r["inputs"]):
            continue
        for in_id, cnt in r["inputs"]:
            bag_take(in_id, cnt)
        npc["gold"] -= min(npc["gold"], gold_need)
        aff = rng.choice(AFFIX_POOL) if rng.random() < 0.14 else None
        self_bag_add(npc, r["out"], 0, r["yield"], aff)
        out_name = db.items.get(r["out"], {}).get("name", r["outName"])
        if calc_expected_price(db, r["out"], 0, market) >= 350_000:
            ticker.append(f"⚒️【巧手鍛造】「{npc['name']}」湊齊材料打造出【{out_name}】！")
            new_events.append(f"剛去妖精森林找工匠做出了一件 {out_name}！")
        break


def upgrade_npc_gear_and_enchant(
    npc: dict[str, Any],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    ticker: list[str],
    new_events: list[str],
    ench_bonus: int = 0,
) -> None:
    cls = npc["cls"]
    eq = npc["eq"]
    bag = npc["bag"]

    # A. 從背包挑更好的裝備換上
    for key in list(bag.keys()):
        if bag.get(key, 0) <= 0:
            continue
        iid_s, ench_s, aff_k = key.split(":")
        iid, ench = int(iid_s), int(ench_s)
        if not db.can_equip(cls, iid):
            continue
        it = db.items[iid]
        slot = "weapon" if it["kind"] == "weapon" else it.get("slot", "armor")
        if slot == "ring":
            slot = "ring1" if "ring1" not in eq else "ring2"
        # 雙手武器不與盾共存
        if slot == "shield" and "weapon" in eq and db.items[eq["weapon"]["id"]].get("type") in TWO_HAND_WEAPONS:
            continue

        new_score = db.item_power_score(cls, iid, ench) + (2.5 if aff_k else 0.0)
        cur = eq.get(slot)
        cur_score = db.item_power_score(cls, cur["id"], cur.get("ench", 0)) if cur else -1.0
        if new_score > cur_score + 0.5:
            # 穿新脫舊
            bag[key] -= 1
            if bag[key] <= 0:
                del bag[key]
            if cur:
                # 若舊裝身上有怪物插卡，換裝時若付得出拔卡費則自動退回圖鑑
                old_sk = ((npc.get("mcards") or {}).get("sock") or {}).pop(slot, None)
                if old_sk:
                    fee_tot = sum(_mcard_unsock_gold(int(c.get("s", 1)), db) for c in old_sk)
                    if npc["gold"] >= fee_tot:
                        npc["gold"] -= fee_tot
                        own_mc = npc.setdefault("mcards", {"own": {}, "sock": {}}).setdefault("own", {})
                        for c in old_sk:
                            arr_mc = own_mc.setdefault(str(c["m"]), [0, 0, 0])
                            arr_mc[max(0, min(2, int(c.get("s", 1)) - 1))] += 1
                # 初始白武不再丟拍賣，直接回收
                if cur["id"] != db.startw.get(cls, 172) or cur.get("ench", 0) > 0:
                    cur_aff = {"k": cur["aff"]} if cur.get("aff") else None
                    self_bag_add(npc, cur["id"], cur.get("ench", 0), 1, cur_aff)
            new_eq = {"id": iid, "ench": ench, "n": 1}
            if aff_k:
                new_eq["aff"] = aff_k
            eq[slot] = new_eq

    # B. 用打到/買到的卷軸衝裝：所有階層全身安定值以內皆一次補滿；過安定維持每 tick 各 1 次高風險衝裝
    w_scr = f"{SCROLL_WEAPON_NORMAL}:0:"
    bw_scr = f"{SCROLL_WEAPON_BLESSED}:0:"
    a_scr = f"{SCROLL_ARMOR_NORMAL}:0:"
    ba_scr = f"{SCROLL_ARMOR_BLESSED}:0:"

    is_rich_scalper = (npc["tier"] == "scalper" and npc["gold"] >= 30_000_000)
    self_over = 2 if (npc["tier"] == "whale" or (npc["tier"] == "scalper" and npc["gold"] >= 60_000_000)) else 1 if (npc["tier"] == "regular" or is_rich_scalper) else 0
    gold_reserve = 150_000 if npc["tier"] == "scalper" else 50_000

    def _take_or_buy_pre_safe_scroll(kind: str) -> tuple[bool, bool]:
        n_k = w_scr if kind == "weapon" else a_scr
        b_k = bw_scr if kind == "weapon" else ba_scr
        scr_cost = 85_000 if kind == "weapon" else 41_000
        if bag.get(n_k, 0) > 0:
            bag[n_k] -= 1
            if bag[n_k] <= 0:
                del bag[n_k]
            return True, False
        if npc["gold"] >= scr_cost + gold_reserve:
            npc["gold"] -= scr_cost
            return True, False
        if bag.get(b_k, 0) > 0:
            bag[b_k] -= 1
            if bag[b_k] <= 0:
                del bag[b_k]
            return True, True
        return False, False

    def _enchant_to_safe(obj: dict[str, Any], kind: str, safe: int) -> None:
        while safe > 0 and obj.get("ench", 0) < safe:
            ok, blessed = _take_or_buy_pre_safe_scroll(kind)
            if not ok:
                break
            cur_e = obj.get("ench", 0)
            p = _npc_enchant_step_prob(kind, cur_e, safe, blessed=blessed)
            if rng.random() < p:
                obj["ench"] = cur_e + 1
            else:
                break

    def _try_oversafe_slot(slot_key: str, kind: str) -> None:
        obj = eq.get(slot_key)
        if not obj:
            return
        it = db.items.get(obj["id"], {})
        default_safe = 6 if kind == "weapon" else 4
        safe = max(0, int(it.get("safe", default_safe)))
        cur_e = obj.get("ench", 0)
        max_try = min(ENCH_MAX, safe + self_over + ench_bonus)
        if safe <= 0 or cur_e < safe or cur_e >= max_try:
            return
        n_k = w_scr if kind == "weapon" else a_scr
        b_k = bw_scr if kind == "weapon" else ba_scr
        use_key = b_k if bag.get(b_k, 0) > 0 else n_k if bag.get(n_k, 0) > 0 else None
        if not use_key:
            return
        blessed = use_key == b_k
        bag[use_key] -= 1
        if bag[use_key] <= 0:
            del bag[use_key]
        p = _npc_enchant_step_prob(kind, cur_e, safe, blessed=blessed)
        if rng.random() < p:
            obj["ench"] = cur_e + 1
            if obj["ench"] >= 7:
                if kind == "weapon":
                    ticker.append(f"🔥【衝裝成功】「{npc['name']}」將【+{obj['ench']} {it['name']}】強化成功！")
                    new_events.append(f"剛把 {it['name']} 衝到 +{obj['ench']}，心情超好！")
                else:
                    ticker.append(f"✨【防具神話】「{npc['name']}」強化出【+{obj['ench']} {it.get('name', '防具')}】！")
        else:
            if kind == "weapon":
                ticker.append(f"💥【化為光影】「{npc['name']}」衝 +{cur_e+1} {it['name']} 失敗爆掉了……")
                new_events.append(f"剛衝 +{cur_e+1} {it['name']} 爆了，正在奇岩發呆療傷…")
                eq["weapon"] = {"id": db.startw.get(cls, 172), "ench": 0, "n": 1}
            else:
                del eq[slot_key]
            ((npc.get("mcards") or {}).get("sock") or {}).pop(slot_key, None)

    # B1. 先將身上武器與所有可安全強化防具補滿至安定值（全員皆點滿）
    w_obj = eq.get("weapon")
    if w_obj:
        w_safe = max(0, int(db.items.get(w_obj["id"], {}).get("safe", 6)))
        _enchant_to_safe(w_obj, "weapon", w_safe)

    arm_slots = [s for s in eq.keys() if s != "weapon"]
    for _sl in arm_slots:
        _a_obj = eq.get(_sl)
        if not _a_obj:
            continue
        _asafe = max(0, int(db.items.get(_a_obj["id"], {}).get("safe", 4)))
        if _asafe > 0:
            _enchant_to_safe(_a_obj, "armor", _asafe)

    # B2. 過安定高風險強化：武器 1 次＋從已達安定值且未滿上限的防具中挑 1 件衝過安定
    _try_oversafe_slot("weapon", "weapon")
    over_arm_slots = []
    for _sl in arm_slots:
        _a_obj = eq.get(_sl)
        if not _a_obj:
            continue
        _asafe = max(0, int(db.items.get(_a_obj["id"], {}).get("safe", 4)))
        _amax = min(ENCH_MAX, _asafe + self_over + ench_bonus)
        if _asafe > 0 and _asafe <= _a_obj.get("ench", 0) < _amax:
            over_arm_slots.append(_sl)
    if over_arm_slots and (bag.get(a_scr, 0) > 0 or bag.get(ba_scr, 0) > 0):
        _try_oversafe_slot(rng.choice(over_arm_slots), "armor")


def scalper_gamble_resell_gear(
    npc: dict[str, Any],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    ticker: list[str],
) -> None:
    """奇岩商人偶爾拿背包多餘白／藍裝或安定 0 飾品（戒/鍊/耳/臂甲）買捲賭強化轉賣"""
    if npc["tier"] != "scalper" or npc["gold"] < 5_000_000 or rng.random() >= 0.28:
        return
    bag = npc.get("bag") or {}
    cands: list[tuple[str, int, int, str, int, dict[str, Any]]] = []
    for key, cnt in list(bag.items()):
        if cnt <= 0:
            continue
        iid_s, ench_s, aff_k = key.split(":")
        iid, ench = int(iid_s), int(ench_s)
        if iid in db.no_sell or iid in db.no_drop or iid == 172:
            continue
        it = db.items.get(iid)
        if not it or it.get("kind") not in ("weapon", "armor"):
            continue
        safe = int(it.get("safe", 6 if it.get("kind") == "weapon" else 4))
        if safe < 0 or ench > safe + 1:
            continue
        est = calc_expected_price(db, iid, ench, market)
        if safe > 0 and est < 25_000:
            continue
        cands.append((key, iid, ench, aff_k, safe, it))
    if not cands:
        return

    cands.sort(key=lambda c: (bag.get(c[0], 0), calc_expected_price(db, c[1], c[2], market)), reverse=True)
    key, iid, cur_e, aff_k, safe, it = rng.choice(cands[: min(5, len(cands))])
    kind = it.get("kind", "armor")
    scr_id = SCROLL_WEAPON_NORMAL if kind == "weapon" else SCROLL_ARMOR_NORMAL
    scr_cost = db.shop_anchor.get(scr_id, 85_000 if kind == "weapon" else 41_000)
    if npc["gold"] < scr_cost * 3 + 2_000_000:
        return

    bag[key] -= 1
    if bag[key] <= 0:
        del bag[key]

    start_e = cur_e
    exploded = False
    while True:
        if cur_e >= safe + 1:
            cont_p = 0.45 if safe == 0 else 0.52
            if cur_e >= safe + 2:
                cont_p = (0.35 if safe == 0 else 0.42) * (0.62 ** (cur_e - (safe + 2)))
            if rng.random() >= cont_p:
                break
        if npc["gold"] < scr_cost + 2_000_000:
            break
        npc["gold"] -= scr_cost
        p = _npc_enchant_step_prob(kind, cur_e, safe, blessed=False, uncapped=True)
        if rng.random() < p:
            cur_e += 1
        elif cur_e >= safe:
            exploded = True
            break

    if not exploded:
        aff_obj = next((a for a in AFFIX_POOL if a["k"] == aff_k), None) if aff_k else None
        self_bag_add(npc, iid, cur_e, 1, aff_obj)
        if cur_e > start_e and ((safe == 0 and cur_e >= 2) or (safe > 0 and cur_e >= safe + 2)):
            ticker.append(f"🎲【商人加工】「{npc['name']}」拿庫存試手氣，點出【+{cur_e} {it['name']}】上架轉賣！")
    elif cur_e >= max(1, safe + 1):
        ticker.append(f"💥【加工成灰】「{npc['name']}」將庫存 {it['name']} 衝 +{cur_e+1} 失敗蒸發……")


def restock_npc_shop(
    npc: dict[str, Any],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    tick_id: int,
    state: dict[str, Any],
) -> None:
    shop: list[dict[str, Any]] = npc.setdefault("shop", [])
    # 移除掛太久沒賣掉的舊貨（48 ticks 打 9 折但不破保底價；滿 60 ticks 退回背包以免本金與貨物蒸發）
    kept: list[dict[str, Any]] = []
    for lst in shop:
        age = tick_id - lst.get("tick", tick_id)
        if age >= 48:
            # 記錄流拍壓力供物價下修
            is_mc = "mcard" in lst
            mk = f"mc_{lst['mcard']}_{lst.get('star', 1)}" if is_mc else f"{lst['item']}_{lst.get('ench', 0)}"
            state.setdefault("unsoldPress", {})[mk] = state.get("unsoldPress", {}).get(mk, 0) + 1
            if age < 60:
                if is_mc:
                    est_l = mcard_expected_price(db, int(lst["mcard"]), int(lst.get("star", 1)), state) * max(1, lst.get("n", 1))
                else:
                    est_l = calc_live_price(db, lst["item"], lst.get("ench", 0), market, state) * max(1, lst.get("n", 1))
                floor_r = 0.75 if npc["tier"] == "scalper" else 0.72
                floor_p = max(50, int(est_l * floor_r))
                lst["price"] = max(floor_p, int(lst["price"] * 0.90))
                kept.append(lst)
            else:
                if is_mc:
                    own_mc = npc.setdefault("mcards", {"own": {}, "sock": {}}).setdefault("own", {})
                    mc_arr = own_mc.setdefault(str(lst["mcard"]), [0, 0, 0])
                    mc_s = max(1, min(3, int(lst.get("star", 1))))
                    mc_arr[mc_s - 1] = int(mc_arr[mc_s - 1]) + max(1, int(lst.get("n", 1)))
                else:
                    self_bag_add(npc, lst["item"], lst.get("ench", 0), lst.get("n", 1), lst.get("aff"))
        else:
            kept.append(lst)
    shop[:] = kept

    max_slots = 15 if npc["tier"] == "scalper" else 11 if npc["tier"] == "whale" else 9 if npc["tier"] == "regular" else 8
    bag = npc["bag"]
    lo_m, hi_m = TIER_SELL_MULT[npc["tier"]]

    # 先統計背包內多餘未鑲嵌之怪物卡（優先上架多餘 3★；僅在全身插槽已滿 3★ 後才釋出零散 2★，避免提早賣光 2★ 合成種子）
    mcard_cands: list[tuple[int, int]] = []
    if db.mcards_cat:
        own_mc = (npc.get("mcards") or {}).get("own") or {}
        sock_mc = (npc.get("mcards") or {}).get("sock") or {}
        eq_now = npc.get("eq") or {}
        all_ge3 = bool(eq_now) and all(
            len(sock_mc.get(sl) or []) >= lim and all(int(c.get("s", 1)) >= 3 for c in (sock_mc.get(sl) or [])[:lim])
            for sl, lim in db.msock_slots.items()
            if sl in eq_now
        )
        for mk, arr in own_mc.items():
            if not isinstance(arr, list) or len(arr) < 3:
                continue
            mid = int(mk)
            if mid not in db.mcard_by_id:
                continue
            if int(arr[2]) > 0 and not any(s.get("mcard") == mid and int(s.get("star", 1)) == 3 for s in shop):
                mcard_cands.append((mid, 3))
            elif (
                int(arr[1]) == 1
                and all_ge3
                and not any(s.get("mcard") == mid and int(s.get("star", 1)) == 2 for s in shop)
            ):
                mcard_cands.append((mid, 2))
        mcard_cands.sort(key=lambda x: (x[1], mcard_expected_price(db, x[0], x[1], state)), reverse=True)
    reserve_mc_slots = min(2, len(mcard_cands))
    bag_slot_cap = max(0, max_slots - reserve_mc_slots)

    # 奸商優先高價／高流通品擺滿 15 格，避免貨架被便宜雜物卡死；一般玩家保留 1-2 張捲自用，其餘賣掉換金幣
    keys = list(bag.keys())
    if npc["tier"] == "scalper":
        # 背包爆倉過多 (>45 種) 時，商人會將過剩低流動普通庫存批發變現 (45%)，防止死貨卡住現金
        if len(keys) > 45:
            rng.shuffle(keys)
            while len(keys) > 40:
                dk = keys.pop()
                dc = bag.get(dk, 0)
                if dc <= 0:
                    continue
                di_s, de_s, _ = dk.split(":")
                di, de = int(di_s), int(de_s)
                if di in db.items and di not in db.no_sell and di not in db.no_drop:
                    d_est = calc_expected_price(db, di, de, market)
                    if d_est < 350_000:
                        npc["gold"] = min(TIER_GOLD_CAP[npc["tier"]], npc["gold"] + max(20, int(d_est * 0.45)) * dc)
                        del bag[dk]
        keys = list(bag.keys())
        keys.sort(
            key=lambda k: calc_expected_price(db, int(k.split(":")[0]), int(k.split(":")[1]), market) * rng.uniform(0.65, 1.35),
            reverse=True,
        )
    else:
        rng.shuffle(keys)
    for key in keys:
        cnt = bag.get(key, 0)
        if cnt <= 0:
            continue
        iid_s, ench_s, aff_k = key.split(":")
        iid, ench = int(iid_s), int(ench_s)
        it = db.items.get(iid)
        if not it or iid in db.no_sell or iid in db.no_drop:
            bag.pop(key, None)
            continue

        # 過低價值生活雜物（< 300 金，如食物/煙火/燈）直接丟遊戲商店換成水錢
        base_est = calc_expected_price(db, iid, ench, market)
        est = calc_live_price(db, iid, ench, market, state)
        if base_est < 300 and it.get("kind") == "etc" and it.get("type") not in ("material", "gem", "scroll", "spellbook", "arrow"):
            npc["gold"] = min(TIER_GOLD_CAP[npc["tier"]], npc["gold"] + max(15, base_est // 2) * cnt)
            del bag[key]
            continue

        # 留少量材料自合（稀有材料如巨魔血若有多或商人則直接上架賣），強化捲自留 1~2 張
        if iid in (SCROLL_ARMOR_NORMAL, SCROLL_WEAPON_NORMAL) and npc["tier"] in ("whale", "regular"):
            if cnt <= 2:
                continue
            sell_n = min(cnt - 2, rng.choice([1, 2, 3]))
        elif it.get("kind") != "etc":
            sell_n = 1
        elif base_est >= 100_000:
            sell_n = 1 if cnt == 1 else min(cnt, rng.choice([1, 2]))
        elif base_est >= 15_000:
            sell_n = min(cnt, rng.choice([1, 2, 3, 5]))
        else:
            sell_n = min(cnt, rng.choice([2, 3, 5, 10]))

        if len(shop) >= bag_slot_cap:
            break

        # 非堆疊品逐件上架
        if it.get("kind") == "etc" and not it.get("stack", 1):
            sell_n = 1

        # 防止同一 NPC 同品項擺太多格（消耗品與+0裝備最多 1 格；高強化裝最多 2 格，避免重複戒指/白裝佔死 15 格）
        same_cnt = sum(1 for s in shop if s.get("item") == iid and s.get("ench", 0) == ench)
        if same_cnt >= (1 if (it.get("kind") == "etc" or ench == 0) else 2):
            continue

        mult = rng.uniform(lo_m, hi_m)
        if npc["tier"] in ("casual", "regular"):
            mult *= float((state.get("dailyEvent") or {}).get("sellUrgency", 1.0))
        aff_bonus = 1.18 if aff_k else 1.0
        unit_price = max(35, int(round(est * mult * aff_bonus / 5.0) * 5))
        # 若為鎮上 NPC 商店常駐無限販售品，價格不應高過商店原價的 94%
        if iid in db.shop_anchor and it.get("kind") == "etc":
            unit_price = min(unit_price, max(10, int(db.shop_anchor[iid] * (0.95 if npc["tier"] == "scalper" else 0.88))))
        if iid in (172, 40743, 40319):  # 不賣新手白弓/箭/精靈玉垃圾
            del bag[key]
            continue

        seq = state["nextSeq"]
        state["nextSeq"] += 1
        listing: dict[str, Any] = {
            "id": f"w_{seq}",
            "item": iid,
            "ench": ench,
            "n": sell_n,
            "price": unit_price * sell_n,
            "tick": tick_id,
        }
        if aff_k:
            aff_obj = next((a for a in AFFIX_POOL if a["k"] == aff_k), None)
            if aff_obj:
                listing["aff"] = aff_obj
        shop.append(listing)
        bag[key] -= sell_n
        if bag[key] <= 0:
            del bag[key]

    # 上架多餘未鑲嵌怪物卡（先扣庫存再進個人商店，與道具同樣依 tier 溢價／折價定價）
    if mcard_cands and len(shop) < max_slots:
        own_mc = (npc.get("mcards") or {}).get("own") or {}
        for mid, star in mcard_cands:
            if len(shop) >= max_slots:
                break
            arr = own_mc.get(str(mid))
            if not arr or int(arr[star - 1]) <= 0:
                continue
            est_mc = mcard_expected_price(db, mid, star, state)
            mult = rng.uniform(lo_m, hi_m)
            if npc["tier"] in ("casual", "regular"):
                mult *= float((state.get("dailyEvent") or {}).get("sellUrgency", 1.0))
            unit_p = max(1_000, int(round(est_mc * mult / 5.0) * 5))
            arr[star - 1] = int(arr[star - 1]) - 1
            if sum(arr) <= 0:
                own_mc.pop(str(mid), None)
            seq = state["nextSeq"]
            state["nextSeq"] += 1
            shop.append({
                "id": f"w_{seq}",
                "mcard": mid,
                "star": star,
                "n": 1,
                "price": unit_p,
                "tick": tick_id,
            })


def run_npc_market_clearing(
    npcs: list[dict[str, Any]],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    ticker: list[str],
    state: dict[str, Any] | None = None,
) -> None:
    """NPC 之間彼此在各自商店買貨（奸商掃低價貨、肝帝補衝裝捲/升級裝、預設 10% 手續費銷毀防通膨）"""
    ev = (state or {}).get("dailyEvent") or {}
    tax_rate = float(ev.get("taxRate", 0.10))
    scalper_buy_max = float(ev.get("scalperBuyMax", 0.80))
    # 蒐集所有賣單
    all_listings: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for seller in npcs:
        for lst in seller.get("shop", []):
            all_listings.append((seller, lst))
    if not all_listings:
        return

    rng.shuffle(npcs)
    sold_ids: set[str] = set()

    for buyer in npcs[:35]:  # 每輪約半數活躍買家逛街
        tier = buyer["tier"]
        tol = TIER_BUY_TOL[tier]
        # 若商人貨架已經近滿 (13+ 格)，暫停低價囤貨但富商仍可買自用升級裝
        scalper_arbitrage_ok = not (tier == "scalper" and len(buyer.get("shop", [])) >= 13)
        # 買家每次看 24~32 件（商人看 32~44 件），提供更活躍的逛街成交量
        view_n = rng.randint(32, 44) if tier == "scalper" else rng.randint(24, 32)
        max_buys = 3 if tier == "whale" else 2 if tier == "regular" else 1
        bought_here = 0
        sample = rng.sample(all_listings, min(view_n, len(all_listings)))
        for seller, lst in sample:
            if seller["id"] == buyer["id"] or lst["id"] in sold_ids:
                continue
            cost = lst["price"]
            if buyer["gold"] < cost + (500_000 if tier == "scalper" else 50_000):
                continue

            if "mcard" in lst:
                mid = int(lst["mcard"])
                star = max(1, min(3, int(lst.get("star", 1))))
                qty_mc = max(1, int(lst.get("n", 1)))
                est = mcard_expected_price(db, mid, star, state) * qty_mc
                ratio = cost / max(1, est)
                want = False
                if tier == "scalper" and scalper_arbitrage_ok and ratio <= scalper_buy_max and star >= 2:
                    want = True
                elif tier in ("whale", "regular") or (tier == "scalper" and buyer["gold"] >= 30_000_000):
                    eff_tol = 1.15 if tier == "scalper" else tol
                    if ratio <= eff_tol:
                        if _npc_mcard_wants_upgrade(buyer, db, mid, star):
                            want = True
                        elif tier in ("whale", "regular") and star == 2:
                            own_b = ((buyer.get("mcards") or {}).get("own") or {}).get(str(mid), [0, 0, 0])
                            if int(own_b[1]) == 1:
                                want = True
                elif tier == "casual" and star >= 2 and ratio <= 0.90 and cost <= max(150_000, buyer["gold"] // 3):
                    if _npc_mcard_wants_upgrade(buyer, db, mid, star):
                        want = rng.random() < 0.35

                if want:
                    buyer["gold"] -= cost
                    net_get = int(cost * (1.0 - tax_rate))
                    tax_paid = max(0, cost - net_get)
                    seller["gold"] = min(TIER_GOLD_CAP[seller["tier"]], seller["gold"] + net_get)
                    own_b = buyer.setdefault("mcards", {"own": {}, "sock": {}}).setdefault("own", {})
                    arr_b = own_b.setdefault(str(mid), [0, 0, 0])
                    arr_b[star - 1] = int(arr_b[star - 1]) + qty_mc
                    sold_ids.add(lst["id"])
                    seller["shop"] = [x for x in seller["shop"] if x["id"] != lst["id"]]
                    disp_mc, _ = _listing_display(db, lst)
                    if state is not None:
                        tick_now = int(state.get("tick", 0))
                        state.setdefault("tradeLog", []).append({
                            "t": tick_now,
                            "bId": buyer["id"],
                            "b": buyer["name"],
                            "bt": buyer["tier"],
                            "sId": seller["id"],
                            "s": seller["name"],
                            "st": seller["tier"],
                            "id": mid,
                            "mcard": mid,
                            "star": star,
                            "nm": disp_mc,
                            "e": 0,
                            "n": qty_mc,
                            "aff": None,
                            "p": cost,
                            "tax": tax_paid,
                            "r": round(ratio, 2),
                        })
                    if cost >= 400_000:
                        ticker.append(f"🃏【怪卡成交】「{buyer['name']}」向「{seller['name']}」買下【{disp_mc}】({cost:,}金)！")
                    bought_here += 1
                    if bought_here >= max_buys:
                        break
                continue

            iid = lst["item"]
            ench = lst.get("ench", 0)
            est = calc_live_price(db, iid, ench, market, state or {}) * max(1, lst.get("n", 1))
            ratio = cost / max(1, est)

            want = False
            if tier == "scalper" and scalper_arbitrage_ok and ratio <= scalper_buy_max:
                it_sc = db.items.get(iid, {})
                if it_sc.get("type") in ("material", "gem", "scroll", "spellbook", "potion") or est >= 30_000:
                    want = True
            elif tier in ("whale", "regular") or (tier == "scalper" and buyer["gold"] >= 30_000_000):
                it = db.items.get(iid, {})
                eff_tol = 1.15 if tier == "scalper" else tol
                if ratio <= eff_tol:
                    if it.get("kind") in ("weapon", "armor") and db.can_equip(buyer["cls"], iid):
                        slot = "weapon" if it["kind"] == "weapon" else it.get("slot", "armor")
                        if slot == "ring":
                            r1, r2 = buyer["eq"].get("ring1"), buyer["eq"].get("ring2")
                            s1 = db.item_power_score(buyer["cls"], r1["id"], r1.get("ench", 0)) if r1 else -1.0
                            s2 = db.item_power_score(buyer["cls"], r2["id"], r2.get("ench", 0)) if r2 else -1.0
                            cur = r1 if s1 <= s2 else r2
                            cur_s = min(s1, s2)
                        else:
                            cur = buyer["eq"].get(slot)
                            cur_s = db.item_power_score(buyer["cls"], cur["id"], cur.get("ench", 0)) if cur else -1.0
                        cand_s = db.item_power_score(buyer["cls"], iid, ench)
                        if cur is None and iid in (20281, 20288):
                            cand_s = max(cand_s, 3.0)
                        if cand_s > cur_s + (0.5 if cur is None else 2.0):
                            want = True
                    elif tier in ("whale", "regular") and it.get("type") in ("material", "gem", "scroll", "spellbook", "potion"):
                        want = rng.random() < (0.55 if tier == "whale" else 0.40)
            elif tier == "casual" and ratio <= 0.85 and cost < 120_000:
                want = rng.random() < 0.25

            if want:
                buyer["gold"] -= cost
                net_get = int(cost * (1.0 - tax_rate))
                tax_paid = max(0, cost - net_get)
                seller["gold"] = min(TIER_GOLD_CAP[seller["tier"]], seller["gold"] + net_get)  # 交易稅直接銷毀（金幣黑洞）
                self_bag_add(buyer, iid, ench, lst.get("n", 1), lst.get("aff"))
                sold_ids.add(lst["id"])
                seller["shop"] = [x for x in seller["shop"] if x["id"] != lst["id"]]
                if state is not None:
                    tick_now = int(state.get("tick", 0))
                    nm_t = db.items.get(iid, {}).get("name", f"#{iid}")
                    aff_obj = lst.get("aff")
                    state.setdefault("tradeLog", []).append({
                        "t": tick_now,
                        "bId": buyer["id"],
                        "b": buyer["name"],
                        "bt": buyer["tier"],
                        "sId": seller["id"],
                        "s": seller["name"],
                        "st": seller["tier"],
                        "id": iid,
                        "nm": nm_t,
                        "e": ench,
                        "n": lst.get("n", 1),
                        "aff": aff_obj.get("name") if isinstance(aff_obj, dict) else None,
                        "p": cost,
                        "tax": tax_paid,
                        "r": round(ratio, 2),
                    })
                # 寫入大額成交快報
                if cost >= 400_000:
                    nm = db.items.get(iid, {}).get("name", f"#{iid}")
                    disp = f"+{ench} {nm}" if ench > 0 else nm
                    ticker.append(f"💰【市集成交】「{buyer['name']}」向「{seller['name']}」買下【{disp}】({cost:,}金)！")
                bought_here += 1
                if bought_here >= max_buys:
                    break

    if state is not None and state.get("tradeLog"):
        cutoff_t = int(state.get("tick", 0)) - 66
        state["tradeLog"] = [tr for tr in state["tradeLog"] if tr.get("t", 0) > cutoff_t]


def update_market_prices(npcs: list[dict[str, Any]], db: GameDatabase, market: dict[str, int]) -> None:
    """根據目前全服供給、流拍狀況與人均金幣，動態調整 marketPrice 基準並抑制通膨"""
    supply: dict[str, int] = {}
    for npc in npcs:
        for lst in npc.get("shop", []):
            if "mcard" in lst:
                continue
            mk = f"{lst['item']}_{lst.get('ench', 0)}"
            supply[mk] = supply.get(mk, 0) + 1

    adv = [n["gold"] for n in npcs if n["tier"] != "scalper"]
    avg_gold = sum(adv) / max(1, len(adv))
    # 依一般冒險人口人均金幣做柔和阻尼（排除囤現金的大盤商人，避免誤壓全服基準物價）
    damp = 0.992 if avg_gold > 22_000_000 else 1.003 if avg_gold < 3_000_000 else 1.0

    by_iid: dict[int, list[tuple[int, str]]] = {}
    for mk, cur_p in list(market.items()):
        iid_s, ench_s = mk.split("_")
        iid, ench = int(iid_s), int(ench_s)
        cnt = supply.get(mk, 0)
        if cnt >= 5:
            adj = 0.978
        elif cnt == 0:
            adj = 1.008
        else:
            adj = 1.0
        new_p = int(cur_p * adj * damp)
        base_p = db.market_base.get(iid, 100)
        init_p = _expected_ench_price(db, iid, ench) if ench > 0 else base_p
        lo = max(20, int(init_p * 0.60))
        hi = max(lo * 3, int(init_p * (1.45 if ench == 0 else 2.20)))
        if iid in db.shop_anchor and db.items.get(iid, {}).get("kind") == "etc" and ench == 0:
            hi = min(hi, int(db.shop_anchor[iid] * 0.92))
            lo = min(lo, max(10, int(db.shop_anchor[iid] * 0.55)))
        market[mk] = max(lo, min(hi, int(round(new_p / 5.0) * 5)))
        by_iid.setdefault(iid, []).append((ench, mk))

    # 高強化單調保護：同道具高一階價格至少維持前一階的 1.25×（避免缺貨導致 +7 比 +8 貴）
    for iid, entries in by_iid.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda x: x[0])
        prev_e, prev_mk = entries[0]
        for ench, mk in entries[1:]:
            min_p = int(round((market[prev_mk] * (1.25 ** (ench - prev_e))) / 5.0) * 5)
            if market[mk] < min_p:
                market[mk] = min_p
            prev_e, prev_mk = ench, mk


def optionally_ai_enrich(world: dict[str, Any]) -> None:
    """選配 AI API 擴充點：只在環境變數設有 GEMINI_API_KEY 且有下 --ai 時呼叫一次 Batch 潤飾"""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return
    # 若為 Gemini Key，單次 Batch 請 AI 潤飾 3 則世界新聞（失敗立即退回原範本）
    try:
        if os.environ.get("GEMINI_API_KEY"):
            sample_npcs = [f"{n['name']}({CLS_ZH[n['cls']]} Lv{n['lv']}): {n['talk']}" for n in world["npcs"][:5]]
            prompt = (
                "你是經典《天堂》台服奇岩村的酒館記者，請根據以下幾位冒險者今天的近況，"
                "用簡短有趣、貼近老玩家語氣的一句話寫 2 條奇岩廣場即時八卦快報，以 JSON 字串陣列回傳：\n"
                + "\n".join(sample_npcs)
            )
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
            body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                txt = data["candidates"][0]["content"]["parts"][0]["text"]
                m = re.search(r"\[.*\]", txt, re.S)
                if m:
                    lines = json.loads(m.group(0))
                    if isinstance(lines, list):
                        for line in lines[:2]:
                            world["ticker"].insert(0, f"🗞️【奇岩日報】{line}")
    except Exception:
        pass


def build_public_world(state: dict[str, Any], db: GameDatabase, use_ai: bool = False) -> dict[str, Any]:
    npcs_pub: list[dict[str, Any]] = []
    vil_cnt = 0
    bat_cnt = 0
    lvs: list[int] = []
    market: dict[str, int] = state.get("market", {})

    for npc in state["npcs"]:
        st = npc.get("state", "village")
        if st == "village":
            vil_cnt += 1
        else:
            bat_cnt += 1
        lvs.append(npc["lv"])

        hp_g, mp_g = npc_base_hp_mp(npc["cls"], npc["lv"], npc["stats"])
        eq_list = []
        for slot, item in npc["eq"].items():
            entry: dict[str, Any] = {"id": item["id"], "n": 1, "ench": item.get("ench", 0), "eq": slot}
            if item.get("aff"):
                entry["aff"] = item["aff"]
            eq_list.append(entry)
        w_eq = npc["eq"].get("weapon") or {"id": db.startw.get(npc["cls"], 172), "ench": 0}

        shop_pub = []
        for lst in npc.get("shop", []):
            if "mcard" in lst:
                shop_pub.append({
                    "id": lst["id"],
                    "mcard": int(lst["mcard"]),
                    "star": int(lst.get("star", 1)),
                    "n": lst.get("n", 1),
                    "price": lst["price"],
                })
                continue
            s_entry: dict[str, Any] = {
                "id": lst["id"],
                "item": lst["item"],
                "ench": lst.get("ench", 0),
                "n": lst.get("n", 1),
                "price": lst["price"],
            }
            if lst.get("aff"):
                s_entry["aff"] = lst["aff"]
            shop_pub.append(s_entry)

        msock_pub: dict[str, list[dict[str, int]]] = {}
        raw_sock = (npc.get("mcards") or {}).get("sock") or {}
        for sk_slot, sk_list in raw_sock.items():
            if sk_slot in npc["eq"] and sk_slot in db.msock_slots and sk_list:
                msock_pub[sk_slot] = [{"m": int(c["m"]), "s": int(c["s"])} for c in sk_list[: db.msock_slots[sk_slot]]]
        mc_sum = npc_mcard_summary(npc)

        npc_pub_row: dict[str, Any] = {
            "id": npc["id"],
            "name": npc["name"],
            "cls": npc["cls"],
            "sex": npc["sex"],
            "tier": npc["tier"],
            "lv": npc["lv"],
            "hpG": hp_g,
            "mpG": mp_g,
            "gold": npc["gold"],
            "stats": npc["stats"],
            "eq": eq_list,
            "weapon": {"id": w_eq["id"], "n": 1, "ench": w_eq.get("ench", 0), "eq": "weapon"},
            "state": st,
            "talk": npc.get("talk", ""),
            "shout": npc.get("shout"),
            "shop": shop_pub,
        }
        if msock_pub:
            npc_pub_row["msock"] = msock_pub
        if mc_sum.get("t", 0) > 0:
            npc_pub_row["mcSum"] = mc_sum
        if npc.get("whaleRetire"):
            npc_pub_row["whaleRetire"] = True
        npcs_pub.append(npc_pub_row)

    # 輸出全服部分關鍵物價行情給前端「向 NPC 兜售」查價用（已套用當日事件 × 股市 × 天氣倍率）
    price_map: dict[str, int] = {}
    for iid in db.items:
        price_map[str(iid)] = calc_live_price(db, iid, 0, market, state)
    for mk, mv in market.items():
        # 例如 "180_7"
        iid_mk = int(mk.split("_")[0])
        price_map[mk] = max(15, int(round(mv * item_event_mult(db, state, iid_mk) / 5.0) * 5))

    now_iso = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    world = {
        "version": f"sim_{state['tick']:05d}",
        "tick": state["tick"],
        "updatedAt": now_iso,
        "stats": {
            "totalNpcs": len(npcs_pub),
            "onlineVillage": vil_cnt,
            "onlineBattle": bat_cnt,
            "avgLv": round(sum(lvs) / max(1, len(lvs)), 1),
            "retiredCount": state.get("retiredCount", 0),
            "retiredByTier": {t: int((state.get("retiredByTier") or {}).get(t, 0)) for t in TIER_COUNTS},
            "totalListings": sum(len(n["shop"]) for n in npcs_pub),
        },
        "dailyEvent": state.get("dailyEvent"),
        "marketRef": price_map,
        "npcs": npcs_pub,
        "ticker": list(reversed(state.get("ticker", [])[-10:])),
    }
    if use_ai:
        optionally_ai_enrich(world)
    return world


def main() -> None:
    parser = argparse.ArgumentParser(description="70 NPC 模擬器：生成本機/GitHub Actions world_state.json")
    base_dir = Path(__file__).resolve().parent.parent
    parser.add_argument("--data-dir", type=Path, default=base_dir / "2_輕量版", help="遊戲前端資料來源目錄")
    parser.add_argument("--state-file", type=Path, default=Path(__file__).resolve().parent / "npc_state.json", help="私有模擬狀態保存檔")
    parser.add_argument("--ticks", type=int, default=1, help="每次執行推進幾個虛擬小時 (預設 1)")
    parser.add_argument("--days", type=float, default=0, help="推進天數（會覆蓋 --ticks，如 --days 7）")
    parser.add_argument("--reset", action="store_true", help="重置世界並重跑開服狀態")
    parser.add_argument("--warmup-ticks", type=int, default=1000, help="重置時的暖機虛擬小時數（設 0 則 70 人全員自 tick=0 Lv.1 起步）")
    parser.add_argument("--seed", type=int, default=None, help="固定亂數種子方便測試重現")
    parser.add_argument("--retire-lv", type=int, default=50, help="滿級退坑門檻等級 (預設 50)")
    parser.add_argument("--offline", action="store_true", help="停用外部氣象/股市連線，純以內建亂數演算")
    parser.add_argument("--ai", action="store_true", help="有設定 API KEY 時啟用單次 Batch AI 新聞潤飾")
    parser.add_argument(
        "--out",
        type=Path,
        nargs="*",
        default=[
            base_dir / "2_輕量版" / "world_state.json",
            base_dir / "1_完整版" / "world_state.json",
        ],
        help="輸出 world_state.json 的目標路徑清單",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    db = GameDatabase(args.data_dir)

    state_file: Path = args.state_file
    state_file.parent.mkdir(parents=True, exist_ok=True)

    is_fresh = args.reset or not state_file.exists()
    if is_fresh:
        print(f"[初始化] 從 {args.data_dir} 載入 {len(db.items)} 件道具、{len(db.monsters)} 隻怪物、{len(db.stages)} 張地圖")
        all_rosters = make_npc_roster(db, rng)
        rng.shuffle(all_rosters)
        warmup_ticks = max(0, int(args.warmup_ticks))
        cohorts = [all_rosters[0:22], all_rosters[22:42], all_rosters[42:58], all_rosters[58:70]]
        state = {
            "tick": 0,
            "retiredCount": 0,
            "retiredByTier": {t: 0 for t in TIER_COUNTS},
            "nextSeq": 1000,
            "market": {},
            "ticker": [],
            "npcs": list(all_rosters) if warmup_ticks < 4 else list(cohorts[0]),
        }
        if warmup_ticks > 0:
            if warmup_ticks >= 4:
                c1_t = min(warmup_ticks - 1, max(1, round(warmup_ticks * 0.144)))
                c2_t = min(warmup_ticks - 1, max(c1_t + 1, round(warmup_ticks * 0.252)))
                c3_t = min(warmup_ticks - 1, max(c2_t + 1, round(warmup_ticks * 0.312)))
            else:
                c1_t = c2_t = c3_t = -1
            print(f"[暖機] 進行 {warmup_ticks} 小時開服生態預演（7 職業各 10 人，分批入服打造各等級梯度）...")
            for t in range(warmup_ticks):
                if t == c1_t:
                    state["npcs"].extend(cohorts[1])
                elif t == c2_t:
                    state["npcs"].extend(cohorts[2])
                elif t == c3_t:
                    state["npcs"].extend(cohorts[3])
                allow_live = (not args.offline) and (t >= max(0, warmup_ticks - 24))
                simulate_tick(state, db, rng, args, allow_live_event=allow_live)
    else:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        for _npc in state.get("npcs", []):
            ensure_npc_mcards(_npc, db, rng)
        ensure_whale_retire_elites(state.get("npcs", []))

    run_ticks = int(round(args.days * 24)) if args.days > 0 else max(0, args.ticks)
    if not is_fresh or args.days > 0 or args.warmup_ticks == 0:
        for step_i in range(run_ticks):
            # 只在當次模擬的最後 24 tick（即「今天」）抓取真實台北天氣與台美股，歷史大量預演不洗 API
            is_today_window = (not args.offline) and (run_ticks <= 24 or step_i >= run_ticks - 24)
            simulate_tick(state, db, rng, args, allow_live_event=is_today_window)
        if run_ticks == 0 and (not args.offline) and state.get("dailyEvent"):
            refresh_daily_event_signals(state)

    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    world = build_public_world(state, db, use_ai=args.ai)
    world_json = json.dumps(world, ensure_ascii=False, separators=(",", ":"))
    for out_path in args.out:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(world_json, encoding="utf-8")

    # 統計驗證摘要
    by_cls = {c: 0 for c in CLASSES}
    by_tier = {t: 0 for t in TIER_COUNTS}
    lvs = []
    for n in world["npcs"]:
        by_cls[n["cls"]] += 1
        by_tier[n["tier"]] += 1
        lvs.append(n["lv"])
    avg_gold = sum(n["gold"] for n in world["npcs"]) // len(world["npcs"])
    print(f"[完成] tick={world['tick']} | 總 NPC={len(world['npcs'])} | 等級 {min(lvs)}~{max(lvs)} (均 {world['stats']['avgLv']}) | 人均金幣 {avg_gold:,}")
    print(f"       職業分布={by_cls}")
    print(f"       生態分布={by_tier}")
    print(f"       個人商店架上總貨件={world['stats']['totalListings']} | 累計滿級退坑替換={world['stats']['retiredCount']} 人")
    for out_path in args.out:
        print(f"       輸出={out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
