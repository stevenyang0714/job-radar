# 求職者 Profile（職缺評分用）

> 這份檔案給評分模型讀。每個新職缺都會對照這裡，判斷最符合哪個求職方向、打分，並建議要投哪一版 CV。

## 1. 我是誰

- Steven Yang，INSEAD MBA（2026 年 7 月畢業），Kellogg MBA 交換（AI strategy）
- 7+ 年經驗，核心能力：**數位轉型、AI 治理、資訊安全／科技風險**，能從策略一路做到落地
- **Deloitte Taiwan Risk Advisory（2021–2024）**：3.5 年升到 Senior Manager（兩次提前升遷），帶 6 人跨國團隊完成 70+ 個科技風險、資安治理、法遵案件，客戶以金融、醫療、製造為主，涵蓋台灣、新加坡、泰國、香港、美國；台灣金管會零信任指引（2024）與數位身分驗證指引（2023）主筆；零信任業務做成 US$4M 服務線
- **iKala Senior Manager（2024–2026，2025 年 5 月起在 CEO Office）**：企業 AI 產品組合 0→1（3 條產品線）、推出 Nexus 地端 AI 一體機（與 Leadtek、NVIDIA 合作）、12 個月建立 US$2M+ pipeline、親自成交 US$1.5M+；與高階主管建立 ISO 42001 AI 治理框架，主導集團 AI 轉型
- **醫療背景**：台大生醫工程碩士、長庚生技學士；共同創辦 SaMD 新創，臨床軟體上線 4 家醫院；顧問期間 6+ 個醫療專案；ScaleX 協助藥廠進入新加坡與馬來西亞市場（HSA、NPRA 法規評估）
- **證照**：ISO/IEC 42001 LA、ISO/IEC 23894、CISA、ISO/IEC 27001 LA、ISO/IEC 27799、BS 10012 LA、ISC2 CC
- **語言**：中文母語、英文流利
- **地點**：台灣、新加坡、香港；APAC 區域職也可以

## 2. 求職方向（Tracks）

### Track A｜金融業 AI 治理・科技風險・資安
- 目標職稱：AI Governance Lead/Manager、Responsible AI、Technology Risk Manager、IT/Cyber Risk、Information Security Governance、Tech Risk & Controls（1LoD/2LoD）、Model Risk（AI 相關）、Digital Risk
- 產業：銀行、保險、證券、支付、fintech、監理機關
- 加分：MAS TRM、HKMA、金管會、零信任、KYC/AML、ISO 42001
- 建議 CV：`CV_StevenYang_Fin_Governance_v2`

### Track B｜醫藥・醫療・醫材的 AI 治理與數位轉型
- 目標職稱：AI Governance（pharma/medtech）、Digital Transformation Manager、Data & AI Strategy、Digital Health Strategy、Healthcare AI Product/GTM、Innovation Lead、Commercial Excellence（數位方向）
- 產業：藥廠、醫材、數位醫療、醫院體系、CRO、healthtech
- 加分：SaMD、醫療資料隱私、HSA/NPRA、medtech
- 建議 CV：治理或數位轉型類 → `CV_StevenYang_Governance_General`；產品、GTM、策略類 → `CV_StevenYang_HC_AI_GTM`

### Track C｜科技公司 AI 導入顧問・解決方案・商業化
- 目標職稱：AI Transformation Consultant、AI Solutions/Deployment Lead、Customer Outcome/Success Manager（AI）、Engagement Manager、Partner/Business Development（AI）、AI GTM Lead、Product Manager（enterprise AI）、Strategy & Operations
- 產業：AI 公司、雲端、AI 基礎設施與硬體、企業 SaaS
- 建議 CV：客戶導入與交付成果 → `CV_StevenYang_AI_Outcome_Manager`；BD、合作夥伴、銷售 → `CV_StevenYang_AI_BDM`；產品、GTM、策略營運 → `CV_StevenYang_AI_GTM_Product`

### Track D｜顧問公司策略・企業內部顧問・轉型專案管理
- 目標職稱：Strategy Consultant/Manager、Digital Transformation Consultant、Technology Consulting（AI/Cyber）、In-house Consulting、Transformation Office、Program Manager、TPM、Chief of Staff
- 公司類型：策略顧問、四大、科技顧問、企業內部策略或轉型部門
- 建議 CV：專案、轉型管理、TPM → `CV_StevenYang_ProgramManager`；風險或科技顧問 → `CV_StevenYang_Governance_General`；策略顧問 → `CV_StevenYang_AI_GTM_Product`

## 3. 資歷層級
- 目標：Manager、Senior Manager、Lead、Associate Director；MBA 後的 Associate（策略顧問）也接受
- 降分：Junior、Analyst（非 MBA associate）、Entry level、Graduate programme
- 層級偏高（Director、VP 且要求 15 年以上經驗）：不排除，標記為 stretch

## 4. 排除條件（不推播）
- 主要工作是寫程式的工程職（Software Engineer、ML Engineer、Data Engineer）
- 需要醫師、藥師、護理執照的臨床職
- 與 AI、科技、顧問無關的純業務職
- 實習、兼職
- 必須具備中文、英文以外語言（例如日文、韓文、印尼文）
- 地點不在台灣、新加坡、香港，且不接受 APAC 遠端

## 5. 評分方式（0–100）

總分 = 職能匹配 × 35% + 產業匹配 × 25% + 資歷層級 × 20% + 地點與語言 × 10% + 加分項 × 10%

每個子項先各自打 0–100 分，再加權加總。

- 70 分以上：即時推播
- 50–69 分：放進每日摘要
- 50 分以下：不通知

## 6. 輸出格式（JSON）

```json
{
  "track": "A | B | C | D",
  "score": 0,
  "sub_scores": {
    "function": 0,
    "industry": 0,
    "seniority": 0,
    "location_language": 0,
    "bonus": 0
  },
  "cv_version": "建議使用的 CV 檔名",
  "fit_points": ["符合點 1", "符合點 2", "符合點 3"],
  "gaps": ["主要缺口"],
  "red_flags": ["需要注意的地方，沒有就留空"]
}
```

## 7. 新加坡職缺的額外條件
- 必須能申請 Employment Pass（EP）：寫明只限新加坡公民／PR 的職缺不推播（硬篩已排除）
- 月薪需能達到 SGD 10,000 以上：
  - 有公開薪資（例如 MyCareersFuture）：薪資上限 ≥ 10,000 即符合
  - 沒有公開薪資：依職級、公司規模、產業估計，並在 red_flags 註明「薪資為估計」；明顯低於 10,000 的職位（例如非 MBA 的 Analyst）降分
- 仲介刊登、看不到實際雇主：在 red_flags 註明
- 約聘（Contract）：可以接受，但在 red_flags 註明
