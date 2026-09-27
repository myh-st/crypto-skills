# Crypto Skills

ภาษา: [English](README.md) · ไทย

ชุด Codex skills สำหรับวิเคราะห์ตลาดคริปโต วางแผนการเข้าออก และบริหารความเสี่ยงจากข้อมูลที่ตรวจสอบได้

repository นี้มี production skill หลักชื่อ `crypto-market-trading-analysis` โดยนำ workflow แบบ analyst → bull/bear research → trader → risk committee → portfolio decision มาปรับให้เหมาะกับตลาดคริปโต ซึ่งต้องดู spot flow, leverage, funding, liquidation, tokenomics และ regime ของ BTC ร่วมกัน

## เป้าหมายการออกแบบ

- แยกข้อเท็จจริงออกจากการตีความด้วย evidence ledger
- ให้ price structure และ spot participation เป็นแกนหลัก แล้วใช้ derivatives อธิบายความเปราะบางของราคา
- ให้ Bull และ Bear โต้แย้งจาก evidence ชุดเดียวกัน ไม่สร้าง narrative คนละชุด
- แยก “ทิศทาง” ออกจาก “จังหวะเข้า” เพราะสินทรัพย์อาจ bullish แต่จังหวะปัจจุบันยังควร `WAIT FOR PULLBACK`
- กำหนด entry, invalidation และ target จากระดับราคา/สภาพคล่อง/volatility ที่สังเกตได้จริง
- รักษา point-in-time integrity สำหรับการวิเคราะห์ย้อนหลังและ backtest
- บันทึก decision และ outcome เพื่อเรียนรู้ว่า timing, leverage และ thesis แบบใดทำงานใน regime ใด
- วิเคราะห์เชิงลึกภายใน แต่ตอบผู้ใช้แบบสั้น กระชับ และเริ่มจาก decision

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

## โครงสร้าง repository

```text
crypto-skills/
├── README.md                           # English
├── README.th.md                        # ภาษาไทย
├── docs/
│   └── architecture.md                 # workflow และขอบเขตการทำงาน
├── schemas/
│   ├── analysis-output.schema.json     # สัญญา output ของ decision
│   ├── decision-record.schema.json     # สัญญา journal / outcome
│   └── evidence-ledger.schema.json    # สัญญาของ fact ledger
├── examples/
│   ├── analysis-output.yaml
│   ├── decision-record.yaml
│   └── evidence-ledger.yaml
├── scripts/
│   └── validate_repo.py                # ตรวจโครงสร้างโดยไม่พึ่ง dependency
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
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/crypto-market-trading-analysis
```

คำสั่งแรกตรวจ repository contracts และ section สำคัญของ workflow ส่วนคำสั่งที่สองตรวจ frontmatter, naming และ scaffold hygiene ของ Codex skill

## แนวทาง contribution

ใส่ domain guidance ที่ใช้ซ้ำได้ไว้ใน `SKILL.md` หรือ reference ที่เจาะจง เก็บ machine-readable contracts ไว้ใน `schemas/`, ตัวอย่างไว้ใน `examples/` และ deterministic checks ไว้ใน `scripts/` ห้าม commit credential, exchange secret, private portfolio data หรือ market snapshot ที่ generate ขึ้นมา กฎ decision ใหม่ควรอธิบาย evidence, สมมติฐานด้าน data quality และ failure mode เสมอ
