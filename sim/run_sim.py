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
   - casual (20): 新手探險家 5~10 場/天（急售便宜貨 0.65~0.85×）
   - scalper (10): 奇岩商人 8~15 場/天（低掃 <0.80×、高賣 1.15~1.35×）
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
TIER_ZH = {
    "whale": "重課肝帝",
    "regular": "一般冒險者",
    "casual": "新手探險家",
    "scalper": "奇岩商人",
}
TIER_RUNS_PER_DAY = {
    "whale": (35, 50),
    "regular": (12, 25),
    "casual": (5, 10),
    "scalper": (8, 15),
}
TIER_SELL_MULT = {
    "whale": (1.00, 1.10),
    "regular": (0.95, 1.05),
    "casual": (0.65, 0.85),
    "scalper": (1.15, 1.35),
}
TIER_BUY_TOL = {
    "whale": 1.25,
    "regular": 1.03,
    "casual": 0.90,
    "scalper": 0.80,
}

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


def ench_rate(kind: str, ench: int, safe: int, blessed: bool = False) -> float:
    """復刻 game.js:enchRate 的真實成功率公式"""
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
    if ench >= ENCH_MAX:
        ch = 0.003 if blessed else 0.0
    return max(0.0, min(1.0, ch))


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

        # 5. game.js (動態抽取掉落倍率、禁掉品、寶箱池、ITEMFX 特效、商店價格與關卡池)
        self._parse_game_constants(game_txt)
        self._parse_item_fx(game_txt)
        self._parse_shop_anchors(game_txt)
        self._parse_stages(game_txt)
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
        for line in m_st.group(1).splitlines():
            mid_m = re.search(r"mapId:\s*(\d+)", line)
            nm_m = re.search(r"name:\s*['\"]([^'\"]+)['\"]", line)
            if not mid_m or not nm_m:
                continue
            mid = int(mid_m.group(1))
            name = nm_m.group(1)
            # 跳過 GM 活動地圖與純單王房（保留主要練功/打王主線地圖）
            if mid in (252, 253, 254, 2005, 610) or "nopool: 1" in line:
                continue

            rec_lv = self.rec.get(mid, 10)
            pool_ids: list[int] = []
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
                continue

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

            self.stages.append(
                {
                    "mapId": mid,
                    "name": name,
                    "recLv": rec_lv,
                    "pool": pool_ids,
                    "bossId": boss_id if boss_id in self.monsters_by_id else None,
                }
            )
        self.stages.sort(key=lambda s: (s["recLv"], s["mapId"]))
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


def calc_expected_price(db: GameDatabase, iid: int, ench: int, market: dict[str, int]) -> int:
    key = f"{iid}_{ench}"
    if key in market:
        return market[key]
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
    res = max(base, int(round(val / 10.0) * 10))
    market[key] = res
    return res


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


def create_new_npc(idx: int, cls: str, tier: str, used_names: set[str], db: GameDatabase, rng: random.Random) -> dict[str, Any]:
    name = pick_unused_name(used_names, rng, tier)
    base_st = dict(CLASS_BASE_STATS[cls])
    for k in base_st:
        base_st[k] = max(7, min(20, base_st[k] + rng.choice([-1, 0, 0, 1])))
    start_w = db.startw.get(cls, 172)
    eq = {"weapon": {"id": start_w, "ench": 0, "n": 1}}
    gold = 0

    return {
        "id": f"npc_{idx:02d}",
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
        "lastEvent": f"剛加入奇岩冒險的小{CLS_ZH[cls]}",
        "state": "village",
    }


def make_npc_roster(db: GameDatabase, rng: random.Random) -> list[dict[str, Any]]:
    # 7 職業各 10 位，總計 70 位；同時把 15/30/15/10 均勻打散至各職業
    tier_deck: list[str] = []
    for t, c in TIER_COUNTS.items():
        tier_deck.extend([t] * c)
    rng.shuffle(tier_deck)

    used_names: set[str] = set()
    npcs: list[dict[str, Any]] = []
    idx = 1
    for ci, cls in enumerate(CLASSES):
        for j in range(10):
            tier = tier_deck[ci * 10 + j]
            npcs.append(create_new_npc(idx, cls, tier, used_names, db, rng))
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
        it = db.items.get(featured["item"], {})
        nm = it.get("name", f"道具#{featured['item']}")
        disp = f"+{featured['ench']} {nm}" if featured.get("ench", 0) > 0 else nm
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


def choose_stage(npc: dict[str, Any], db: GameDatabase, rng: random.Random) -> dict[str, Any]:
    lv = npc["lv"]
    tier = npc["tier"]
    tier_offset = 6 if tier == "whale" else 2 if tier == "regular" else -2 if tier == "casual" else 0
    target_rec = max(8, lv + tier_offset)
    # 排除超高 Lv80+ 單挑龍窟直到後期 42 級以上
    cands = [s for s in db.stages if s["recLv"] <= target_rec + 5]
    if not cands:
        cands = [db.stages[0]]
    # 偏向挑選接近目標推薦等級的地圖
    cands.sort(key=lambda s: abs(s["recLv"] - target_rec))
    top = cands[: min(4, len(cands))]
    return rng.choice(top)


def simulate_tick(state: dict[str, Any], db: GameDatabase, rng: random.Random, args: argparse.Namespace) -> dict[str, Any]:
    state["tick"] = state.get("tick", 0) + 1
    tick_id = state["tick"]
    npcs: list[dict[str, Any]] = state["npcs"]
    market: dict[str, int] = state.setdefault("market", {})
    ticker: list[str] = state.setdefault("ticker", [])
    retired_total = state.get("retiredCount", 0)

    # 確保所有 70 人都有合理市場基準
    used_names = {n["name"] for n in npcs}

    # --- 1. 推進每名 NPC 1 小時打怪／升級／掉寶／消耗 ---
    next_listing_seq = state.setdefault("nextSeq", 1000)

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

        # 記錄最近發生的精彩事蹟
        new_events: list[str] = []
        for _ in range(n_runs):
            npc["runs"] = npc.get("runs", 0) + 1
            st = choose_stage(npc, db, rng)
            pool = st["pool"]
            # 《天堂倖存者》每場擊殺數依戰力分層，拉開重課(速衝50)與一般/新手(緩慢成長)的等級跨度
            if tier == "whale":
                power_factor = min(3.8, 1.05 + npc["lv"] * 0.065)
            elif tier == "regular":
                power_factor = min(2.3, 0.65 + npc["lv"] * 0.035)
            elif tier == "scalper":
                power_factor = min(1.8, 0.55 + npc["lv"] * 0.025)
            else:
                power_factor = min(1.25, 0.35 + npc["lv"] * 0.018)
            kills = int(rng.randint(1200, 2400) * power_factor)
            npc["kills"] = npc.get("kills", 0) + kills

            # 經驗值：從該地圖怪物池隨機抽取樣本估算平均 MEXP
            sample_mons = [db.monsters_by_id[rng.choice(pool)] for _ in range(12)]
            avg_mexp = sum(db.mexp.get(int(m["id"]), 2) for m in sample_mons) / max(1, len(sample_mons))
            elite_ratio = 0.09
            exp_gain = int(kills * avg_mexp * (1 + elite_ratio))

            # 首領判定
            boss_killed = False
            if st["bossId"] and rng.random() < (0.85 if tier == "whale" else 0.65):
                boss_killed = True
                exp_gain += db.bxp.get(st["mapId"], 110)

            # 套用真實 EXPT 升級
            if npc["lv"] < 50:
                npc["exp"] += exp_gain
                while npc["lv"] < 50 and npc["exp"] >= (db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]):
                    npc["exp"] -= db.expt[npc["lv"]] - db.expt[npc["lv"] - 1]
                    npc["lv"] += 1
                    new_events.append(f"剛升上 Lv.{npc['lv']}！離頂點又近了一步")
                    if npc["lv"] >= 50:
                        npc["exp"] = 0
                        ticker.append(f"🎉【滿級賀電】{CLS_ZH[cls]}「{npc['name']}」在{st['name']}達成 Lv.50！")

            # 掉寶與實戰金幣產出（反映高圖大量清怪 + 困難/惡夢掉寶 + 戰利品自動折現）
            drop_rolls = max(12, min(28, 10 + npc["lv"] // 3))
            diff_gold_mult = 2.4 if tier == "whale" else 2.2 if tier == "scalper" else 1.7 if tier == "regular" else 1.2
            run_gold = int((rng.randint(6000, 14000) + st["recLv"] * rng.randint(650, 1300)) * diff_gold_mult)
            for _r in range(drop_rolls):
                mon = db.monsters_by_id[rng.choice(pool)]
                for dr in mon.get("drops", []):
                    iid = int(dr["item"])
                    if iid == GOLD_ID:
                        run_gold += int(rng.randint(int(dr.get("min", 25)), int(dr.get("max", 90))) * 4.5 * diff_gold_mult)
                        continue
                    if iid in db.no_drop or iid not in db.items:
                        continue
                    ch = (int(dr.get("chance", 10000)) * db.matk.get(iid, 1.0)) / 1_000_000.0
                    # 模擬整場約數千隻怪的縮放抽樣：高機率材料常態掉落，極稀有料 (如食人巨魔的血) 維持罕見
                    eff_roll = min(0.28, ch * 3.0)
                    if rng.random() < eff_roll:
                        it = db.items[iid]
                        ench = 0
                        aff = None
                        if it.get("kind") in ("weapon", "armor"):
                            if rng.random() < 0.09:
                                ench = 1
                            if st["recLv"] >= 20 and rng.random() < 0.08:
                                aff = rng.choice(AFFIX_POOL)
                            self_bag_add(npc, iid, ench, 1, aff)
                        else:
                            qty = rng.randint(int(dr.get("min", 1)), int(dr.get("max", 1)))
                            self_bag_add(npc, iid, 0, qty, None)

            if boss_killed and st["bossId"]:
                bmon = db.monsters_by_id[st["bossId"]]
                run_gold += int(rng.randint(15000, 48000) * diff_gold_mult)
                # 開啟首領寶箱/特殊寶箱（依 game.js CHEST_MATS 產出製作材料與祝武/防）
                if rng.random() < 0.45 and db.chest_mats:
                    mat_id = rng.choice(db.chest_mats)
                    if mat_id in db.items:
                        self_bag_add(npc, mat_id, 0, 1, None)
                if rng.random() < 0.015:
                    self_bag_add(npc, 84072, 0, 1, None)  # 純白的萬能藥
                for dr in bmon.get("drops", []):
                    iid = int(dr["item"])
                    if iid == GOLD_ID or iid in db.no_drop or iid not in db.items:
                        continue
                    ch = (int(dr.get("chance", 10000)) * db.matk.get(iid, 1.0)) / 1_000_000.0
                    if rng.random() < min(0.38, ch * 1.1):
                        it = db.items[iid]
                        aff = rng.choice(AFFIX_POOL) if (it.get("kind") in ("weapon", "armor") and rng.random() < 0.18) else None
                        self_bag_add(npc, iid, 0, 1, aff)
                        if db.market_base.get(iid, 0) >= 250_000:
                            ticker.append(f"💎【打王掉寶】「{npc['name']}」推倒{bmon['name']}喜獲【{it['name']}】！")

            # 水錢／傳送／彈藥／基礎強化消耗
            upkeep = 1200 + st["recLv"] * (280 if tier == "whale" else 170 if tier == "regular" else 80)
            npc["gold"] = max(0, npc["gold"] + run_gold - upkeep)

        # 高金幣 NPC 消耗金幣開曼孟 100萬/900萬裝備箱（維持千萬級別流通水位又能買玩家高價貨）
        sink_cap = 22_000_000 if tier == "scalper" else 18_000_000 if tier == "whale" else 7_500_000 if tier == "regular" else 1_800_000
        if npc["gold"] > sink_cap:
            spend = min(npc["gold"] - sink_cap // 2, 3_000_000 if rng.random() < 0.35 else 1_000_000)
            npc["gold"] -= spend
            if spend >= 1_000_000 and db.box_pool:
                bi = rng.choice(db.box_pool)
                be = 4 if rng.random() < 0.05 else 3 if rng.random() < 0.15 else 2 if rng.random() < 0.30 else 0
                if bi in db.items:
                    self_bag_add(npc, bi, be, 1, None)

        # --- 2. 利用 craftdb.js 提煉中間素材或打造製作裝備/餅乾 ---
        craft_npc_items(npc, db, market, rng, ticker, new_events)

        # --- 3. 自動換裝＋強化消耗（裝備／卷軸黑洞）---
        upgrade_npc_gear_and_enchant(npc, db, market, rng, ticker, new_events)

        # --- 3. 整理背包 → 下架過期品／補進個人商店貨架 ---
        restock_npc_shop(npc, db, market, rng, tick_id, state)

        if new_events:
            npc["lastEvent"] = new_events[-1]

    # --- 4. NPC ↔ NPC 跨商店市場交易（10% 稅金銷毀）---
    run_npc_market_clearing(npcs, db, market, rng, ticker)

    # --- 5. Lv.50 退坑循環 → 補進 Lv.1 新手 ---
    for i, npc in enumerate(npcs):
        if npc["lv"] >= 50:
            npc["maxLvTicks"] = npc.get("maxLvTicks", 0) + 1
        # 滿級後逐步光榮退坑（同時若老號退坑比例低於定額，老玩家也有極低機率換血重練），維持全等級生態
        retire_prob = 0.0
        if npc["maxLvTicks"] >= 18:
            retire_prob = 0.07 if npc["tier"] == "whale" else 0.05
        elif npc["ageTicks"] > 180 and npc["lv"] >= 35 and npc["tier"] in ("casual", "regular"):
            retire_prob = 0.008
        if retire_prob > 0 and rng.random() < retire_prob:
            retired_total += 1
            old_name = npc["name"]
            old_cls = npc["cls"]
            old_tier = npc["tier"]
            old_lv = npc["lv"]
            used_names.discard(old_name)
            fresh = create_new_npc(i + 1, old_cls, old_tier, used_names, db, rng)
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
                # 初始白武不再丟拍賣，直接回收
                if cur["id"] != db.startw.get(cls, 172) or cur.get("ench", 0) > 0:
                    cur_aff = {"k": cur["aff"]} if cur.get("aff") else None
                    self_bag_add(npc, cur["id"], cur.get("ench", 0), 1, cur_aff)
            new_eq = {"id": iid, "ench": ench, "n": 1}
            if aff_k:
                new_eq["aff"] = aff_k
            eq[slot] = new_eq

    # B. 用打到/買到的卷軸衝裝（核心爆裝回收水池）
    w_scr = f"{SCROLL_WEAPON_NORMAL}:0:"
    bw_scr = f"{SCROLL_WEAPON_BLESSED}:0:"
    a_scr = f"{SCROLL_ARMOR_NORMAL}:0:"
    ba_scr = f"{SCROLL_ARMOR_BLESSED}:0:"

    # 肝帝 / 一般玩家會自己買卷軸把武器至少衝到安定值 (+6)
    w_obj = eq.get("weapon")
    if w_obj and npc["tier"] in ("whale", "regular") and npc["gold"] >= 90000:
        w_it = db.items[w_obj["id"]]
        safe = max(0, int(w_it.get("safe", 6)))
        target = min(8 if npc["tier"] == "whale" else safe + 1, ENCH_MAX)
        if w_obj.get("ench", 0) < safe and bag.get(w_scr, 0) == 0 and npc["gold"] >= 95000:
            npc["gold"] -= 85000  # 商店/系統回收買卷
            bag[w_scr] = 1

    # 試圖強化武器
    if w_obj:
        w_it = db.items[w_obj["id"]]
        safe = max(0, int(w_it.get("safe", 6)))
        max_try = safe + 2 if npc["tier"] == "whale" else safe + 1 if npc["tier"] == "regular" else safe
        cur_e = w_obj.get("ench", 0)
        if cur_e < max_try:
            use_key = bw_scr if (cur_e >= safe and bag.get(bw_scr, 0) > 0) else w_scr if bag.get(w_scr, 0) > 0 else None
            if use_key:
                blessed = use_key == bw_scr
                bag[use_key] -= 1
                if bag[use_key] <= 0:
                    del bag[use_key]
                p = ench_rate("weapon", cur_e, safe, blessed=blessed)
                if rng.random() < p:
                    w_obj["ench"] = cur_e + 1
                    if w_obj["ench"] >= 7:
                        ticker.append(f"🔥【衝裝成功】「{npc['name']}」將【+{w_obj['ench']} {w_it['name']}】強化成功！")
                        new_events.append(f"剛把 {w_it['name']} 衝到 +{w_obj['ench']}，心情超好！")
                else:
                    # 過安定失敗爆裝！武器降回備用白武
                    if cur_e >= safe:
                        ticker.append(f"💥【化為光影】「{npc['name']}」衝 +{cur_e+1} {w_it['name']} 失敗爆掉了……")
                        new_events.append(f"剛衝 +{cur_e+1} {w_it['name']} 爆了，正在奇岩發呆療傷…")
                        eq["weapon"] = {"id": db.startw.get(cls, 172), "ench": 0, "n": 1}

    # 試圖強化隨機一件防具
    arm_slots = [s for s in eq.keys() if s != "weapon"]
    if arm_slots and (bag.get(a_scr, 0) > 0 or bag.get(ba_scr, 0) > 0):
        slot = rng.choice(arm_slots)
        a_obj = eq[slot]
        a_it = db.items.get(a_obj["id"], {})
        safe = int(a_it.get("safe", 4))
        cur_e = a_obj.get("ench", 0)
        max_try = safe + 2 if npc["tier"] == "whale" else safe + 1 if npc["tier"] == "regular" else safe
        if safe > 0 and cur_e < max_try:
            use_key = ba_scr if (cur_e >= safe and bag.get(ba_scr, 0) > 0) else a_scr if bag.get(a_scr, 0) > 0 else None
            if use_key:
                blessed = use_key == ba_scr
                bag[use_key] -= 1
                if bag[use_key] <= 0:
                    del bag[use_key]
                p = ench_rate("armor", cur_e, safe, blessed=blessed)
                if rng.random() < p:
                    a_obj["ench"] = cur_e + 1
                    if a_obj["ench"] >= 7:
                        ticker.append(f"✨【防具神話】「{npc['name']}」強化出【+{a_obj['ench']} {a_it.get('name','防具')}】！")
                elif cur_e >= safe:
                    del eq[slot]


def restock_npc_shop(
    npc: dict[str, Any],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    tick_id: int,
    state: dict[str, Any],
) -> None:
    shop: list[dict[str, Any]] = npc.setdefault("shop", [])
    # 移除掛太久沒賣掉的舊貨（48 ticks 約 2 天）並退回背包或微調減價
    kept: list[dict[str, Any]] = []
    for lst in shop:
        age = tick_id - lst.get("tick", tick_id)
        if age >= 48:
            # 記錄流拍壓力供物價下修
            mk = f"{lst['item']}_{lst.get('ench', 0)}"
            state.setdefault("unsoldPress", {})[mk] = state.get("unsoldPress", {}).get(mk, 0) + 1
            if age < 60:
                lst["price"] = max(50, int(lst["price"] * 0.90))
                kept.append(lst)
        else:
            kept.append(lst)
    shop[:] = kept

    max_slots = 9 if npc["tier"] == "scalper" else 7 if npc["tier"] == "whale" else 6 if npc["tier"] == "regular" else 5
    bag = npc["bag"]
    lo_m, hi_m = TIER_SELL_MULT[npc["tier"]]

    # 奸商優先高價出清捲軸/藍裝；一般玩家保留 1-2 張捲自用，其餘賣掉換金幣
    keys = list(bag.keys())
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
        est = calc_expected_price(db, iid, ench, market)
        if est < 300 and it.get("kind") == "etc" and it.get("type") not in ("material", "gem", "scroll", "spellbook", "arrow"):
            npc["gold"] += max(15, est // 2) * cnt
            del bag[key]
            continue

        # 留少量材料自合（稀有材料如巨魔血若有多或商人則直接上架賣），強化捲自留 1~2 張
        if iid in (SCROLL_ARMOR_NORMAL, SCROLL_WEAPON_NORMAL) and npc["tier"] in ("whale", "regular"):
            if cnt <= 2:
                continue
            sell_n = min(cnt - 2, rng.choice([1, 2, 3]))
        elif it.get("kind") != "etc":
            sell_n = 1
        elif est >= 100_000:
            sell_n = 1 if cnt == 1 else min(cnt, rng.choice([1, 2]))
        elif est >= 15_000:
            sell_n = min(cnt, rng.choice([1, 2, 3, 5]))
        else:
            sell_n = min(cnt, rng.choice([2, 3, 5, 10]))

        if len(shop) >= max_slots:
            break

        # 非堆疊品逐件上架
        if it.get("kind") == "etc" and not it.get("stack", 1):
            sell_n = 1

        # 防止同一 NPC 同品項擺太多格
        if any(s["item"] == iid and s.get("ench", 0) == ench for s in shop) and it.get("kind") == "etc":
            continue

        mult = rng.uniform(lo_m, hi_m)
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


def run_npc_market_clearing(
    npcs: list[dict[str, Any]],
    db: GameDatabase,
    market: dict[str, int],
    rng: random.Random,
    ticker: list[str],
) -> None:
    """NPC 之間彼此在各自商店買貨（奸商掃低價貨、肝帝補衝裝捲/升級裝、10% 手續費直接銷毀防通膨）"""
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
        # 挑 3 筆來看
        sample = rng.sample(all_listings, min(8, len(all_listings)))
        for seller, lst in sample:
            if seller["id"] == buyer["id"] or lst["id"] in sold_ids:
                continue
            cost = lst["price"]
            if buyer["gold"] < cost + (2_500_000 if tier == "scalper" else 50_000):
                continue
            iid = lst["item"]
            ench = lst.get("ench", 0)
            est = calc_expected_price(db, iid, ench, market) * max(1, lst.get("n", 1))
            ratio = cost / max(1, est)

            want = False
            if tier == "scalper" and ratio <= 0.80:
                want = True
            elif tier in ("whale", "regular") and ratio <= tol:
                it = db.items.get(iid, {})
                if it.get("kind") in ("weapon", "armor") and db.can_equip(buyer["cls"], iid):
                    slot = "weapon" if it["kind"] == "weapon" else it.get("slot", "armor")
                    cur = buyer["eq"].get(slot)
                    cur_s = db.item_power_score(buyer["cls"], cur["id"], cur.get("ench", 0)) if cur else 0
                    if db.item_power_score(buyer["cls"], iid, ench) > cur_s + 2.0:
                        want = True
                elif it.get("type") in ("material", "gem", "scroll", "spellbook", "potion") and ratio <= tol:
                    want = rng.random() < (0.45 if tier == "whale" else 0.30)
            elif tier == "casual" and ratio <= 0.85 and cost < 120_000:
                want = rng.random() < 0.25

            if want:
                buyer["gold"] -= cost
                seller["gold"] += int(cost * 0.90)  # 10% 交易稅直接銷毀（金幣黑洞）
                self_bag_add(buyer, iid, ench, lst.get("n", 1), lst.get("aff"))
                sold_ids.add(lst["id"])
                seller["shop"] = [x for x in seller["shop"] if x["id"] != lst["id"]]
                # 寫入大額成交快報
                if cost >= 400_000:
                    nm = db.items.get(iid, {}).get("name", f"#{iid}")
                    disp = f"+{ench} {nm}" if ench > 0 else nm
                    ticker.append(f"💰【市集成交】「{buyer['name']}」向「{seller['name']}」買下【{disp}】({cost:,}金)！")
                break


def update_market_prices(npcs: list[dict[str, Any]], db: GameDatabase, market: dict[str, int]) -> None:
    """根據目前全服供給、流拍狀況與人均金幣，動態調整 marketPrice 基準並抑制通膨"""
    supply: dict[str, int] = {}
    for npc in npcs:
        for lst in npc.get("shop", []):
            mk = f"{lst['item']}_{lst.get('ench', 0)}"
            supply[mk] = supply.get(mk, 0) + 1

    avg_gold = sum(n["gold"] for n in npcs) / max(1, len(npcs))
    # 若全服平均持有金幣過高，柔和啟動價格上限阻尼防止惡性通膨
    damp = 0.992 if avg_gold > 9_500_000 else 1.003 if avg_gold < 1_800_000 else 1.0

    for mk, cur_p in list(market.items()):
        iid_s, ench_s = mk.split("_")
        iid, ench = int(iid_s), int(ench_s)
        cnt = supply.get(mk, 0)
        if cnt >= 6:
            adj = 0.985
        elif cnt == 0:
            adj = 1.003
        else:
            adj = 1.0
        new_p = int(cur_p * adj * damp)
        base_p = db.market_base.get(iid, 100)
        lo = max(20, int(base_p * 0.60))
        hi = max(lo * 3, int(base_p * (1.45 if ench == 0 else 80.0)))
        if iid in db.shop_anchor and db.items.get(iid, {}).get("kind") == "etc" and ench == 0:
            hi = min(hi, int(db.shop_anchor[iid] * 0.92))
            lo = min(lo, max(10, int(db.shop_anchor[iid] * 0.55)))
        market[mk] = max(lo, min(hi, int(round(new_p / 5.0) * 5)))


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

        npcs_pub.append(
            {
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
        )

    # 輸出全服部分關鍵物價行情給前端「向 NPC 兜售」查價用（精簡只保留常見裝備/捲軸＋market 中已出現項目）
    price_map: dict[str, int] = {}
    for iid in db.items:
        price_map[str(iid)] = calc_expected_price(db, iid, 0, market)
    for mk, mv in market.items():
        # 例如 "180_7"
        price_map[mk] = mv

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
            "totalListings": sum(len(n["shop"]) for n in npcs_pub),
        },
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
    parser.add_argument("--warmup-ticks", type=int, default=336, help="重置時的暖機虛擬小時數（設 0 則 70 人全員自 tick=0 Lv.1 起步）")
    parser.add_argument("--seed", type=int, default=None, help="固定亂數種子方便測試重現")
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
            "nextSeq": 1000,
            "market": {},
            "ticker": [],
            "npcs": list(all_rosters) if warmup_ticks == 0 else list(cohorts[0]),
        }
        if warmup_ticks > 0:
            print(f"[暖機] 進行 {warmup_ticks} 小時開服生態預演（7 職業各 10 人，分批入服打造各等級梯度）...")
            for t in range(warmup_ticks):
                if t == 144:
                    state["npcs"].extend(cohorts[1])
                elif t == 252:
                    state["npcs"].extend(cohorts[2])
                elif t == 312:
                    state["npcs"].extend(cohorts[3])
                simulate_tick(state, db, rng, args)
    else:
        state = json.loads(state_file.read_text(encoding="utf-8"))

    run_ticks = int(round(args.days * 24)) if args.days > 0 else max(0, args.ticks)
    if not is_fresh or args.days > 0 or args.warmup_ticks == 0:
        for _ in range(run_ticks):
            simulate_tick(state, db, rng, args)

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
