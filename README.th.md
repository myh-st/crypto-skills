# Crypto Skills

ภาษา: [English](README.md) · ไทย

ชุด Codex skills สำหรับวิเคราะห์ตลาดคริปโต วางแผนการเข้าออก และบริหารความเสี่ยงจากข้อมูลที่ตรวจสอบได้

repository นี้มี production skill หลักชื่อ `crypto-market-trading-analysis` โดยนำ workflow แบบ analyst → bull/bear research → trader → risk committee → portfolio decision มาปรับให้เหมาะกับตลาดคริปโต ซึ่งต้องดู spot flow, leverage, funding, liquidation, tokenomics และ regime ของ BTC ร่วมกัน

## เป้าหมายการออกแบบ

- แยกข้อเท็จจริงออกจากการตีความด้วย evidence ledger
- ให้ price structure และ spot participation เป็นแกนหลัก แล้วใช้ derivatives อธิบายความเปราะบางของราคา
- ให้ Bull และ Bear โต้แย้งจาก evidence ชุดเดียวกัน ไม่สร้าง narrative คนละชุด
- แยก “ทิศทาง” ออกจาก “จังหวะเข้า” เพราะสินทรัพย์อาจ bullish แต่จังหวะปัจจุบันยังควร `WAIT_FOR_PULLBACK`
- กำหนด entry, invalidation และ target จากระดับราคา/สภาพคล่อง/volatility ที่สังเกตได้จริง
- รักษา point-in-time integrity สำหรับการวิเคราะห์ย้อนหลังและ backtest
- บันทึก decision และ outcome เพื่อเรียนรู้ว่า timing, leverage และ thesis แบบใดทำงานใน regime ใด
- วิเคราะห์เชิงลึกภายใน แต่ตอบผู้ใช้แบบสั้น กระชับ และเริ่มจาก decision
- ใช้ vocabulary ของ final decision state จาก `schemas/decision-state.schema.json` เพียงชุดเดียว

## Architecture

```text
Market / spot / derivatives / options / on-chain / tokenomics / macro
                                │
                                ▼
                    Normalize + timestamp + quality-check
                                │
                                ▼
                         Neutral evidence ledger
                                │
                         ┌──────┴──────┐
                         ▼             ▼
                    Bull thesis    Bear thesis
                         └──────┬──────┘
                                ▼
                         Research judge
                                ▼
                         Execution planner
                    entry / invalidation / targets
                                ▼
                   aggressive / neutral / conservative
                              risk lenses
                                ▼
                         Portfolio decision
                                ▼
              concise answer + monitoring conditions + journal record
```

นี่เป็นการแบ่งบทบาทเชิงตรรกะภายในโมเดลเดียว ไม่ได้บังคับให้ต้องรันหลาย agent แยกกัน

### Interactive diagram

[เปิด architecture diagram แบบ interactive](docs/architecture.html)

![สถาปัตยกรรมจาก evidence ถึง decision ของ Crypto Skills](docs/architecture-preview.png)

## AI Portfolio Trading OS (PAPER)

เริ่มแอปบนเครื่องด้วย `python3 -m crypto_eval paper-server` แล้วเปิด
`http://127.0.0.1:8765/` แอปเป็น cockpit สำหรับพอร์ต PAPER ทั้ง Spot และ
Perpetual มีหน้า Overview, Portfolio, Trade, Activity, Research, Evaluations
และ Settings

- **สินทรัพย์:** เลือกจาก catalog ของ Gate จริง
- **Spot:** มีบัญชีแยก (ต้นทุนเฉลี่ย ค่าธรรมเนียม และ limit ที่ fill บางส่วน)
  ไม่ใช่ futures ที่ leverage 1 เท่า
- **Ticket ที่ผู้ใช้ส่งเอง:** ผ่าน RiskEngine และเส้นทาง fill เดียวกับการเทรดของ AI
- **จัดการ position:** แก้ stop/เป้า ลด/ปิด และกำหนดอำนาจชัดเจน
  (`AUTO_PAPER`, `RECOMMEND_ONLY`, `MANUAL_OVERRIDE`, `PAUSED`)
- **AI re-plan:** แบบมีโครงสร้าง แสดงค่าก่อน/หลัง ให้ Apply / Edit / Reject
- **Portfolio Brain:** ลดขนาดหรือบล็อกได้ แต่ข้าม RiskEngine ไม่ได้
- **ตรวจ position อัตโนมัติ:** คิวรีวิวที่เริ่มจากเงื่อนไขแบบ deterministic
  ไม่เรียก AI ทุก tick
- **บันทึก:** Activity รวม AI/USER/SYSTEM และคิวเรื่องที่ต้องดู
- **การเรียนรู้:** post-trade review และ strategy tournament ที่รวมต้นทุน AI

- **ความปลอดภัยในการ execute:** สถานะตลาด (crash, ผันผวน, ข้อมูลไม่น่าเชื่อถือ)
  การจัดการ print ผิดปกติ ตัววางแผน execute แบบ deterministic (กรอบ slippage,
  แบ่งไม้, จำกัดความเร็วการขาย, ปกป้อง Spot Core) kill switch และการ reconcile บัญชี
  ดู [`docs/crash-execution-safety.md`](docs/crash-execution-safety.md)

- **Spot lifecycle:** สถานะรอบใหญ่แบบมีชนิด (สะสม, ถือ Core, เทรนด์ขยาย, ป้องกันกำไร,
  ทยอยขาย, ลด, ออก) หลักฐาน regime แบบ point-in-time ทยอยขายแทนขายหมดทีเดียว
  ขาย Core เฉพาะเมื่อยืนยันการพังของโครงสร้าง และมี benchmark เทียบแบบเงื่อนไขเดียวกัน
  ดู [`docs/spot-cycle-lifecycle-manager.md`](docs/spot-cycle-lifecycle-manager.md)

การเขียนคำสั่งเงินจริงไปยัง Gate ยังถูกบล็อกโดยการออกแบบ รายละเอียดอยู่ที่
[`docs/ai-portfolio-trading-os.md`](docs/ai-portfolio-trading-os.md)

## โครงสร้าง repository

```text
crypto-skills/
├── README.md                           # English
├── README.th.md                        # ภาษาไทย
├── docs/
│   ├── architecture.md                 # workflow และขอบเขตการทำงาน
│   ├── evaluation.md                   # CLI, metric, ขอบเขตข้อมูล และ experiment
│   ├── paper-futures-runtime.md        # runtime PAPER futures และการตั้งค่าอย่างปลอดภัย
│   └── ai-portfolio-trading-os.md      # cockpit PAPER Spot + Perp, อำนาจ, re-plan, brain
├── schemas/
│   ├── analysis-output.schema.json     # สัญญา output ของ decision
│   ├── decision-state.schema.json      # canonical final decision states
│   ├── decision-record.schema.json     # สัญญา journal / outcome
│   ├── evidence-ledger.schema.json     # สัญญาของ fact ledger
│   └── eval-*.schema.json              # สัญญา evaluation spec / case / prediction / outcome
├── examples/
│   ├── analysis-output.yaml
│   ├── decision-record.yaml
│   └── evidence-ledger.yaml
├── scripts/
│   └── validate_repo.py                # ตรวจโครงสร้างโดยไม่พึ่ง dependency
├── crypto_eval/                        # evaluation harness และ fixture CLI
├── eval/
│   └── specs/crypto-market-v1.json     # เป้าหมาย walk-forward หลายสินทรัพย์
├── tests/
│   └── test_contracts.py               # regression tests ของ contract/evaluation
├── .github/workflows/
│   └── validate.yml                     # gate สำหรับ PR/push
└── skills/
    └── crypto-market-trading-analysis/
        ├── SKILL.md                    # คำสั่งของ Codex skill ฉบับเต็ม
        ├── agents/openai.yaml           # UI metadata และ invocation policy
        ├── examples/                    # ตัวอย่างเฉพาะ skill
        └── references/                   # operating contracts แบบ progressive disclosure
```

## ติดตั้งสำหรับ Codex

จาก checkout นี้ ให้ link skill เข้าโฟลเดอร์ skills ของ Codex:

```bash
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/crypto-market-trading-analysis" \
  ~/.codex/skills/crypto-market-trading-analysis
```

ถ้าไม่สะดวกใช้ symlink สามารถ copy directory ได้ repository นี้ไม่มี credential และไม่เก็บ API key เชื่อมต่อ CoinMarketCap, exchange, options หรือ on-chain data ผ่าน runtime environment แทน

## Prompt ติดตั้งแบบครั้งเดียว

คัดลอก prompt ด้านล่างไปวางใน Codex, Claude Code, Gemini CLI หรือ AI Agent ที่รองรับ เพื่อให้ agent ตรวจ client, ติดตั้ง/อัปเดต skill ใน scope ที่เหมาะสม, ตรวจสอบผล และรายงาน path ที่ติดตั้งจริง โดย prompt จะไม่ขอให้คุณแปะ API key ลงใน repository หรือบทสนทนา

```text
คุณกำลังติดตั้ง repository Crypto Skills เป็น skill สำหรับวิเคราะห์ตลาดคริปโตที่ใช้ซ้ำได้

แหล่งอ้างอิงหลัก (source of truth):
  https://github.com/myh-st/crypto-skills.git
โฟลเดอร์ skill ภายใน repository:
  skills/crypto-market-trading-analysis

เป้าหมาย:
  ตรวจว่า AI client ปัจจุบันคือ Codex, Claude Code, Gemini CLI หรือ agent อื่น
  แล้วติดตั้ง/อัปเดต skill ใน scope ที่รองรับและปลอดภัยที่สุด จากนั้น validate และ
  รายงานสิ่งที่เปลี่ยนแปลงแบบกระชับ ให้ใช้ workspace เป็นค่าเริ่มต้นสำหรับการติดตั้ง
  เฉพาะโปรเจกต์ ใช้ user/global scope เมื่อผู้ใช้ขอโดยตรง หรือ client ไม่มีโฟลเดอร์
  skill ระดับ workspace

กฎความปลอดภัย:
1. ตรวจ OS, working directory, client/version และสถานะ repository ก่อนแก้ไฟล์
   ห้ามใช้ `git reset --hard`, การลบ recursive แบบกว้าง หรือคำสั่งที่เขียนทับไฟล์
   อื่นโดยไม่เกี่ยวข้อง
2. ถ้ายังไม่มี repository ให้ clone ไปยัง directory ที่รายงานได้ชัดเจน ถ้ามีอยู่แล้ว
   ให้ fetch/update เฉพาะเมื่อเป็น repository เดียวกัน และเก็บ uncommitted work ไว้
   บันทึก commit SHA ที่ติดตั้งจริง
3. ต้องติดตั้งทั้งโฟลเดอร์ skill ที่มี `SKILL.md` เพื่อให้ `references/`, `examples/`
   และ metadata ใต้ `agents/` ยังใช้งานได้
4. ถ้าปลายทางมีอยู่แล้ว ให้เปรียบเทียบกับ source ก่อน ใช้ symlink สำหรับ checkout
   ที่กำลังพัฒนา หรือ copy สำหรับ portable install ก่อนแทนที่ directory ที่ไม่ใช่
   source ให้ย้าย directory นั้นไป backup ที่มี timestamp และรายงาน path ให้ผู้ใช้
   ขออนุญาตก่อนการแทนที่ที่ทำลายข้อมูลหรือก่อนใช้สิทธิ์ยกระดับ
5. รัน validator ของ repository: `python3 scripts/validate_repo.py` ตรวจว่า
   `SKILL.md` เริ่มด้วย YAML frontmatter ที่ถูกต้อง และเมื่อติดตั้งสำหรับ Codex
   ต้องมี `agents/openai.yaml` ด้วย ถ้ามี Codex validator ให้รัน
   `uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/crypto-market-trading-analysis` โดยไม่ติดตั้ง package แบบ global

ใช้ integration ตาม client ที่ตรวจพบ:
- Codex: user scope ใช้ `~/.codex/skills/crypto-market-trading-analysis` หรือ
  workspace scope ใช้ `.codex/skills/crypto-market-trading-analysis` แนะนำ symlink
  ไปยัง checkout ระหว่างพัฒนา ถ้าต้อง reload ให้เปิด session ใหม่หรือใช้วิธี reload
  ของ client แล้วตรวจว่าเรียก `$crypto-market-trading-analysis` ได้
- Claude Code: personal scope ใช้ `~/.claude/skills/crypto-market-trading-analysis`
  หรือ project scope ใช้ `.claude/skills/crypto-market-trading-analysis` ตรวจด้วย
  `/skills` และหลังสร้าง top-level skills directory ใหม่ให้ใช้ `/reload-skills`
  เมื่อ client รองรับ
- Gemini CLI: ถ้ามีคำสั่ง `gemini skills` ให้ติดตั้ง subdirectory
  `skills/crypto-market-trading-analysis` (ใช้ repository URL ตรง ๆ เฉพาะเมื่อ client
  รองรับ monorepo subdirectory) หรือ clone ไว้ก่อนแล้วใช้
  `gemini skills link <local-skill-path>` ใช้ `--scope workspace` เฉพาะเมื่อต้องการ
  ติดตั้งในโปรเจกต์ ถ้าไม่มี manager ให้ใช้ `~/.gemini/skills/` หรือ `.gemini/skills/`
  (รองรับ alias `.agents/skills/` ด้วย) แล้วตรวจ `/skills list` และ refresh ด้วย
  `/skills reload`
- AI Agent อื่น: ตรวจ native skill directory ตามเอกสารของ client แล้ววาง skill ไว้ที่นั่น
  ถ้าไม่มี skill manager ให้เก็บ checkout ไว้และโหลด
  `skills/crypto-market-trading-analysis/SKILL.md` เป็น instruction โดยตรง พร้อม
  รายงานว่าเป็น explicit-reference installation ไม่ใช่ native discovery ห้ามรายงานว่าสำเร็จ
  โดยไม่แสดง path จริง

ความปลอดภัยของ CoinMarketCap / MCP:
- ห้ามพิมพ์, commit, embed หรือใส่ API key ไว้ใน prompt, README, shell history,
  log, screenshot หรือไฟล์ที่ generate หากผู้ใช้อนุญาต key แล้ว ให้เก็บเป็น
  `CMC_API_KEY` ใน environment หรือ secret manager ที่ host รองรับ และตรวจด้วย metadata
  request แบบ authenticated ที่ไม่เปิดเผยค่า key
- ถ้ามี CoinMarketCap MCP server อยู่แล้ว ให้ตั้งค่าผ่าน MCP settings ของ client และ
  รายงานชื่อ server กับ read-only tools ถ้าไม่มี อย่าสร้างชื่อ package เองหรือแอบติดตั้ง
  ให้รายงานว่า adapter ยังขาด และปล่อยให้ skill ใช้ data source อื่นได้
- skill นี้เป็น read-only ห้ามส่ง order, โอนเงิน, ขอ private key หรือเปิดสิทธิ์ custody/trading

รายงานเมื่อเสร็จ (ต้องมี):
- client และ install scope ที่ตรวจพบ
- checkout ต้นทางและ commit SHA
- path ที่ติดตั้งจริงและระบุว่าเป็น symlink หรือ copy
- คำสั่ง validate และผล pass/fail
- คำสั่ง reload/restart และตัวอย่าง invocation สั้น ๆ
- สถานะ CMC/MCP โดยไม่เปิดเผย secret
- warning, permission ที่ขาด หรือ native integration ที่ไม่รองรับ
```

เอกสารอ้างอิงตาม client: [Claude Code Skills](https://code.claude.com/docs/en/skills) · [Gemini CLI Agent Skills](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/using-agent-skills.md) โดย prompt จะใช้ repository นี้เป็น source of truth เดียวและปรับขั้นตอนตาม client ที่ตรวจพบ

## วิธีเรียกใช้

เรียก skill โดยตรง:

```text
$crypto-market-trading-analysis วิเคราะห์ SEI/USDT แบบ spot swing พร้อม buy zone, invalidation, targets และ risk
```

ถ้าข้อมูลบางช่องเป็น optional skill จะระบุ assumption แล้ววิเคราะห์ต่อ ไม่หยุดงานโดยไม่จำเป็น สำหรับเงินก้อนใหญ่, leverage, เหรียญสภาพคล่องต่ำ, event สำคัญ หรือเป้าหมายหลายเท่า ระบบจะเพิ่มความลึกของการวิเคราะห์ภายในให้อัตโนมัติ

## รูปแบบคำตอบสำหรับผู้ใช้

คำตอบปกติจะสั้นและเรียงตามนี้:

1. สถานะ decision
2. โซนเข้าหลักและโซนเข้ารอง
3. Invalidation
4. Targets และ horizon
5. เหตุผลสำคัญ 3–5 ข้อ
6. ความเสี่ยงหลัก / เงื่อนไขที่ทำให้มุมมองเปลี่ยน

ถ้าผู้ใช้ขอ deep dive จึงค่อยแสดง market snapshot, evidence ledger, scenario map, decision object และเงื่อนไขที่ทำให้เปลี่ยนใจ ระบบจะไม่ถือ indicator, funding, headline หรือ model confidence เป็นคำรับประกันผลตอบแทน

## ขอบเขตข้อมูลและความปลอดภัย

- ใส่ timestamp, timezone, venue และ instrument ให้ข้อมูลตลาดปัจจุบันทุกครั้ง
- ให้ความสำคัญกับข้อมูลจาก exchange/project โดยตรง และใช้ aggregator สำหรับภาพรวมข้ามตลาด
- Normalize mark/index/last price, OI แบบ USD notional, contract type และ funding interval ก่อนเปรียบเทียบ
- ข้อมูลที่หายหรือ stale ต้องระบุเป็น unavailable/partial ไม่เติมศูนย์และไม่ตีความเป็นสัญญาณ
- การวิเคราะห์ย้อนหลังต้องไม่เห็น candle, unlock, ข่าว หรือ outcome ที่เกิดภายหลัง cutoff
- API key, private account data, การส่งคำสั่ง และ custody อยู่นอกขอบเขตของ read-only skill นี้

## ตรวจสอบการเปลี่ยนแปลง

หลังแก้ skill ให้รัน:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/crypto-market-trading-analysis
```

คำสั่งแรกตรวจ JSON/YAML examples เทียบกับ schema, enum canonical, references ที่จำเป็น
และ section สำคัญของ workflow คำสั่งที่สองรัน regression tests ของ schema/fixture
ส่วนคำสั่งที่สามตรวจ frontmatter, naming และ scaffold hygiene ของ Codex skill
GitHub Actions จะรัน deterministic synthetic evaluation pipeline เพิ่มเติมทุก PR
และทุก push ไป `main` ซึ่งเป็นเพียงการตรวจ harness ไม่ใช่ข้อสรุปเรื่องความแม่นยำ

## Evaluation harness

### Validation != Accuracy Evaluation

`scripts/validate_repo.py` และ unit tests ตรวจโครงสร้าง repository, schema และ
พฤติกรรมที่ทำซ้ำได้เท่านั้น ไม่ได้พิสูจน์ว่า analysis skill แม่นยำ ทำกำไร
หรือดีกว่า control

รัน fixture pipeline แบบ offline ครบทุกขั้นตอน:

```bash
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo
```

คำสั่งนี้สร้าง dataset, freeze fixture predictions, ให้คะแนนจาก outcomes
ที่แยกไฟล์, เปรียบเทียบ baseline และสร้างรายงาน JSON/Markdown รายงานต้องระบุว่า
**DEMO / HARNESS VALIDATION — NOT MARKET PERFORMANCE EVIDENCE** เพราะ fixture
runner ไม่ได้เรียกใช้ analysis skill หรือ model จริง อ่าน
[`docs/evaluation.md`](docs/evaluation.md) สำหรับ CLI แยกแต่ละขั้นตอน,
data contract, denominator ของ metric และวิธีต่อยอด

`eval/specs/crypto-market-v1.json` ระบุเป้าหมาย dataset แบบ chronological
ไม่ shuffle ครอบคลุม BTC, ETH, SOL, SUI, SEI, AVAX และ PYTH ใน regime
bull, bear, range และ high volatility ทุก case ต้องมี `as_of`, `data_cutoff`,
`asset`, `instrument`, `venue` และ `horizon` ชัดเจน snapshot ย้อนหลังจะปฏิเสธ
ข้อมูลอนาคต และข่าวต้องมีเวลา archive ที่ตรวจสอบได้ predictions ถูก freeze
ใน append-only log และ outcomes เก็บแยกกัน sampling manifest ต้องนับ case
ที่กำหนดไว้/included/excluded ให้ครบ พร้อมเหตุผลและ exclusion rule ที่ประกาศไว้
การรอ pullback/breakout ที่ไม่เคย trigger จะไม่ถูกนับเป็น entry ที่ล้มเหลว

รายงานประกอบด้วย directional/trigger-aware metrics, BTC benchmark return/alpha
เมื่อมีข้อมูล, MFE/MAE, เวลาถึง trigger/target, จำนวนตัวอย่างและช่วงความเชื่อมั่น
พร้อม baseline แบบ fixed ได้แก่ Buy & Hold, BTC, EMA20/EMA50, RSI14, naive
และ seeded random ข้อมูลที่ขาดต้องแสดง unavailable ไม่เติมศูนย์ ค่า drawdown
เป็น proxy จากลำดับผลการตัดสินใจแบบน้ำหนักเท่ากัน ไม่ใช่ portfolio PnL
เพราะยังไม่จำลอง sizing, cash, fills, fees, slippage หรือ funding

มีเพียง interface สำหรับต่อ model runner และ read-only data provider เท่านั้น
ไม่มี live market adapter, credential, paid API หรือการเรียก model จริง
ผลย้อนหลังมีความหมายเมื่อ prediction ถูก freeze ก่อนรู้ outcome เท่านั้น
มิฉะนั้นควรใช้ forward paper evaluation การอ้างว่า skill ดีกว่า control
ต้องมี predictions ที่ archive จาก model/config เดียวกัน ใช้ out-of-sample
ตามเวลาและ case เดียวกัน พร้อมจำนวนตัวอย่างเพียงพอ harness เปรียบเทียบ
paired run เหล่านั้นได้ แต่สร้างผลจาก model ให้เองไม่ได้

## แนวทาง contribution

ใส่ domain guidance ที่ใช้ซ้ำได้ไว้ใน `SKILL.md` หรือ reference ที่เจาะจง เก็บ machine-readable contracts ไว้ใน `schemas/`, ตัวอย่างไว้ใน `examples/` และ deterministic checks ไว้ใน `scripts/` ห้าม commit credential, exchange secret, private portfolio data หรือ market snapshot ที่ generate ขึ้นมา กฎ decision ใหม่ควรอธิบาย evidence, สมมติฐานด้าน data quality และ failure mode เสมอ
