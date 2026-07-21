# 预验证外链资源库

> **用途**：当写作素材包 E 类（权威数据引用）URL 不足 2 条时，
> `/write` 自动从此库中按主题标签匹配补充外链。
>
> **维护方式**：人工从常用权威来源中筛选验证后收录。每条必须亲自验证过 URL 有效。
>
> **禁止**：不要收录任何未经验证的链接。

---

## 填写规则

| 字段 | 说明 |
|:---|:---|
| URL | 具体页面的完整 URL（不是根域名） |
| 来源类型 | `.gov` / `.edu` / 行业协会 / 学术期刊 / 权威媒体 / 独立安全组织 / 制造商原厂 |
| 主题标签 | 逗号分隔，用于 `/write` 匹配当前文章主题 |
| 可用内容摘要 | 这条链接提供什么数据/结论（方便 Claude 引用时不跑题） |
| 最后验证日期 | `YYYY-MM-DD`，超过 6 个月需重新验证 |

---

## 外链库

| # | URL | 来源类型 | 主题标签 | 可用内容摘要 | 最后验证 |
|---|-----|----------|----------|-------------|----------|
| 1 | https://www.fda.gov/radiation-emitting-products/laser-products-and-instruments/frequently-asked-questions-about-lasers | .gov | 激光安全, 法规, 分类 | FDA 明确 consumer laser 通常属于 Class I/II/IIIa；蓝/紫激光与绿光同样亮度时通常功率更高 | 2026-05-18 |
| 2 | https://www.ecfr.gov/current/title-21/chapter-I/subchapter-J/part-1040/section-1040.10 | .gov | 激光法规, 标签, 美国 | 21 CFR 1040.10 — 美国激光产品正式法规文本，含标签位置与安全要求 | 2026-05-18 |
| 3 | https://www.faa.gov/about/initiatives/lasers/laws | .gov | 航空安全, 法规, 罚款 | FAA 统计：2024 年 12,840 起 laser strikes；每次最高罚 $11,000，多次累计最高 $30,800 | 2026-05-18 |
| 4 | https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=912603 | .gov | 虚标功率, 测试, 绿激光 | NIST 2013 随机测试 122 支激光笔：89.7% 绿激光不合格，52.4% 超法定限值 2 倍以上 | 2026-05-18 |
| 5 | https://www.nist.gov/publications/random-testing-reveals-excessive-power-commercial-laser-pointers | .gov | 虚标功率, 测试, 安全 | NIST 论文摘要：laser pointers 应限制 Class 3R / 5 mW max，大量产品超标 | 2026-05-18 |
| 6 | https://www.lia.org/resources/laser-safety-standards/ansi-z1361-safe-use-lasers | 行业协会 | 激光安全, 标准, ANSI | ANSI Z136.1 是激光安全程序的基础标准，涵盖 hazard classification、LSO 职责、PPE | 2026-05-18 |
| 7 | https://www.lia.org/resources/laser-safety-information/laser-hazard-classification | 行业协会 | 激光分类, 安全, Class 1-4 | LIA 按 Class 1/2/3R/3B/4 梳理风险等级 | 2026-05-18 |
| 8 | https://pubmed.ncbi.nlm.nih.gov/29271340/ | 学术期刊 | 眼损伤, 医学, 安全 | 综述 111 名患者：激光笔可致持续性视网膜损伤，55% 受影响眼初诊视力低于 20/40 | 2026-05-18 |
| 9 | https://www.laserpointersafety.com/safetycalcs/index.html | 独立安全组织 | 危害距离, 安全计算, NOHD | 5 mW / 1 mrad 激光笔 NOHD = 51.9 feet；提供危害距离计算 | 2026-05-18 |
| 10 | https://www.laserpointersafety.com/illegalpointers/illegalpointers.html | 独立安全组织 | 非法产品, Amazon, 虚标 | 2012 年测试 Amazon 上 24 支激光笔全部非法且超功率 | 2026-05-18 |
| 11 | https://www.gov.uk/government/news/tough-new-penalties-for-misuse-of-lasers | .gov | 英国法规, 处罚, 航空 | 英国政府对激光 misuse 最高判 5 年监禁；CAA 当年收到 1,258 起报告 | 2026-05-18 |
| 12 | https://www.canada.ca/en/health-canada/services/health-risks-safety/radiation/everyday-things-emit-radiation/laser-products.html | .gov | 加拿大法规, 进口, Class 3B/4 | Class 3B 和 Class 4 手持激光在加拿大禁止进口/制造/广告/销售 | 2026-05-18 |
| 13 | https://tc.canada.ca/en/aviation/aviation-security/use-hand-held-lasers-legally-safely | .gov | 加拿大法规, 公共区域, >1 mW | Transport Canada：超过 1 mW 的手持激光在公共区域持有是非法的（除例外情形） | 2026-05-18 |
| 14 | https://www.abf.gov.au/importing-exporting-and-manufacturing/prohibited-goods/list-of-items?srckeyword=weapons | .gov | 澳大利亚法规, 进口, 禁止 | ABF：手持激光笔为禁止进口物品，除非获得许可 | 2026-05-18 |
| 15 | https://files.cie.co.at/841_CIE_TN_004-2016.pdf | 行业协会 | 视见函数, 555nm, 绿光亮度 | CIE TN 004:2016 — 明视觉函数 V(λ)，约 555 nm 时光谱光视效率固定 683 lm/W | 2026-05-18 |
| 16 | https://www.nichia.co.jp/en/product/ld.html | 制造商原厂 | 激光二极管, UV/蓝/绿/红, 上游 | Nichia 官方 LD 产品页面，覆盖多波段激光二极管 | 2026-05-18 |
| 17 | https://look.ams-osram.com/m/5ad3ecdc2686a878/original/PL-TB450B.pdf | 制造商原厂 | 蓝光二极管, 450nm, 1.6W | ams OSRAM PL TB450B datasheet：450 nm 蓝光、典型 1.6 W 光功率 | 2026-05-18 |
| 18 | https://webstore.iec.ch/en/publication/32662 | 国际标准 | 电池安全, 锂电, 便携设备 | IEC 62133-2:2017 便携式密封二次锂电池安全要求与测试 | 2026-05-18 |
| 19 | https://www.osha.gov/laser-hazards/standards | .gov | 工作场所安全, OSHA, 法规 | OSHA 激光危害标准页 — 通过 29 CFR 1910.132/133 和 1926.102(b)(2) 执行激光安全，引用 ANSI Z136.1 为共识标准 | 2026-05-18 |
| 20 | https://www.osha.gov/otm/section-3-health-hazards/chapter-6 | .gov | 工作场所安全, 技术手册, Class 3B/4 | OSHA 技术手册 Section III Chapter 6 — 激光危害详细技术指导 | 2026-05-18 |
| 21 | https://pubmed.ncbi.nlm.nih.gov/28269425/ | 学术期刊 | 虚标功率, 澳大利亚, RMIT | RMIT 大学 (2016): 所有测试绿激光超标 51-127 倍安全限值 | 2026-05-18 |
| 22 | https://pubs.aip.org/lia/jla/article-abstract/34/2/022025/2843006 | 学术期刊 | 虚标功率, 希腊, EEAE | 希腊原子能委员会 (2022): 52% 受测设备超功率限值 2 倍以上 | 2026-05-18 |
| 23 | https://pubs.aip.org/lia/jla/article/38/1/012011/3374103/Laser-products-entering-the-UK-consumer-market | 学术期刊 | 虚标功率, 英国, UKHSA | UKHSA (2026): 英国网络市场上非合规激光产品测试 | 2026-05-18 |
| 24 | https://www.legislation.gov.uk/ukpga/2018/9/contents | .gov | 英国法规, 航空, 刑罚 | Laser Misuse (Vehicles) Act 2018 — 对车辆/飞行器照射激光最高判 5 年 | 2026-05-18 |
| 25 | https://www.gov.uk/government/publications/laser-radiation-safety-advice/laser-radiation-safety-advice | .gov | 英国法规, BS EN 50689, 消费者 | UKHSA 官方激光安全建议 — Class 3B/4 不适合普通消费者 | 2026-05-18 |
| 26 | https://www.legislation.gov.uk/uksi/2010/1140 | .gov | 英国法规, 工作场所, COAOR | Control of Artificial Optical Radiation at Work Regulations 2010 — 雇主义务 | 2026-05-18 |
| 27 | https://standards.iteh.ai/catalog/standards/clc/30fb24a1-d951-4f30-a70b-8d4f8ae018d9/en-50689-2021 | 国际标准 | EU标准, 消费者激光, EN 50689 | EN 50689:2021 — 消费者激光产品安全标准，限制 Class 3B/4 进入消费市场 | 2026-05-18 |
| 28 | https://www.ul.com/insights/understand-new-laser-product-safety-standards-europe | 行业协会 | EU标准, EN 50689, 解释 | UL Solutions 对欧洲新激光产品安全标准的行业解读 | 2026-05-18 |
| 29 | http://galileo.phys.virginia.edu/classes/106/1995/omp/033195.html | .edu | 散射, 光束可见性, Rayleigh scattering | UVA Galileo 物理学 Q&A：激光束可见依赖散射；尘埃/烟雾/雾增强可见性；强激光在洁净空气中也可通过 Rayleigh scattering 可见 | 2026-05-22 |
| 30 | https://ehs.berkeley.edu/laser-safety-manual/laser-classifications | .edu | 激光分类, Class 3B/4, 安全 | UC Berkeley EHS 激光安全手册：3B=5-500mW；Class 4>500mW；眼/皮肤/火灾风险详细说明 | 2026-05-22 |
| 31 | https://www.fda.gov/radiation-emitting-products/home-business-and-entertainment-products/laser-light-shows | .gov | FDA, 演示激光, 娱乐激光, 法规 | FDA 激光演示/娱乐规定：可见波段默认 5mW 限制；高等级需申请 variance | 2026-05-22 |
| 32 | https://www.fda.gov/radiation-emitting-products/home-business-and-entertainment-products/laser-products-and-instruments | .gov | FDA, 激光产品, 分类, 法规 | FDA 激光产品与仪器主页：I-IV 类分类；消费级激光笔常落 3R/3A；show/demo lasers 受 21 CFR 1040.11(c) 约束 | 2026-05-22 |
| 33 | https://www.edmundoptics.com/knowledge-center/application-notes/lasers/beam-expanders/ | 行业光学 | 扩束器, beam divergence, collimator | Edmund Optics：光束扩束器原理 — beam diameter 增大 → divergence 下降；经典口径-发散角权衡 | 2026-05-22 |
| 34 | https://www.nist.gov/news-events/news/2010/08/beware-dim-laser-pointer-nist-researchers-measure-high-infrared-power | .gov | IR泄漏, DPSS, 绿激光安全, NIST | NIST TN 1668 (2010): "10mW" 绿激光因 KTP 晶体未对准发射 20mW 不可见 808nm IR — 足以在察觉前致视网膜损伤 | 2026-05-23 |
| 35 | https://www.fda.gov/radiation-emitting-products/laser-products-and-instruments/important-information-laser-pointer-manufacturers | .gov | FDA, 制造商, 激光笔, 合规 | FDA 明确规定：作为 laser pointer 推广的可见光手持激光限制在 400-710nm、≤5mW；5mW-500mW Class IIIb 不能合法作为指示笔宣传 | 2026-05-23 |
| 36 | https://research.uga.edu/docs/units/safety/laser/Laser-Safety-Manual-Printable.pdf | .edu | ANSI Z136.1, 大学安全手册, 激光安全 | UGA 2023 激光安全手册以 ANSI Z136.1-2022 为主要指导文件，涵盖 hazard classification、LSO 职责、PPE | 2026-05-23 |
| 37 | https://led-ld.nichia.co.jp/api/data/spec/ld/NDB4916.pdf | 制造商原厂 | Nichia, 蓝光二极管, 458nm, 500mW | Nichia NDB4916 官方 datasheet：蓝光二极管典型 458nm，500mW CW，Class 4 | 2026-05-23 |
| 38 | https://led-ld.nichia.co.jp/api/data/spec/ld/NDG7P75.pdf | 制造商原厂 | Nichia, 绿光二极管, 525nm, 2.3W | Nichia NDG7P75 官方 datasheet：绿光二极管典型 525nm，典型 2.3W，范围 518-532nm | 2026-05-23 |
| 39 | https://www.nejm.org/doi/full/10.1056/NEJMc1005818 | 学术期刊 | 眼损伤, 绿激光, 150mW, NEJM | NEJM 病例报告：150mW 绿光手持激光导致严重双眼视网膜损伤；网上可轻易买到高达 700mW 设备 | 2026-05-23 |
| 40 | https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=912781 | .gov | NIST, 手持激光测试, 合规率 | NIST 随机测试 122 支激光笔：89.7% 绿激光不合格，52.4% 超法定限值 2 倍以上 | 2026-05-25 |
| 41 | https://pmc.ncbi.nlm.nih.gov/articles/PMC12958270/ | 学术期刊 | 视网膜损伤, 激光笔, 儿童, 病例系列 | 2025 病例系列：32 例激光笔视网膜损伤；>40% 患者未满 18 岁；部分进展至黄斑裂孔或 CNV | 2026-05-25 |
| 42 | https://www.faa.gov/newsroom/laser-strikes-aircraft-continue-be-dangerously-high | .gov | 航空安全, laser strikes, FAA | FAA 2024 全年统计：12,840 起 laser strikes，328 起受伤，罚款最高 $11,000/次 | 2026-05-29 |
| 43 | https://cie.co.at/eilvterm/17-21-090 | 国际标准 | 视觉峰值, 555nm, photopic, V(λ) | CIE 国际照明委员会：明视觉辐射发光效率最大值对应 555.017nm | 2026-05-29 |
| 44 | https://labsafety.jhu.edu/wp-content/uploads/2017/07/Laser-pointer-fact-sheet-v9-170725FNL.pdf | .edu | JHU, 激光安全, 绿光感知 | JHU 实验室安全：绿光比红光感知约亮 30 倍 ⚠️(bot) PDF 403 反爬 — 需浏览器人工核对 | 2026-05-29 |

---

## 常见权威来源参考

| 类别 | 来源示例 |
|:---|:---|
| 美国政府 | FDA/CDRH, FAA, NIST, eCFR |
| 其他国家政府 | GOV.UK, Health Canada, Transport Canada, ABF (澳大利亚) |
| 行业协会 | ANSI (via LIA), CIE, IEC |
| 学术 | PubMed, Google Scholar |
| 独立安全组织 | LaserPointerSafety.com |
| 制造商原厂 | Nichia, ams OSRAM |

## [引用简述]
- **来源 URL**: https://www.bu.edu/research/ethics-compliance/safety/radiation-safety/radiation-safety-committee/laser-safety-manual/appendix-bu-02-laser-pointer-guidance/
- **可用内容**: - **URL**: https://www.bu.edu/research/ethics-compliance/safety/radiation-safety/radiation-safety-committee/laser-safety-manual/appendix-bu-02-laser-pointer-guidance/
- **适用文章**: 
- **使用次数**: 0


## [引用简述]
- **来源 URL**: https://ehs.dartmouth.edu/laboratory-research-safety/laser-safety-program/laser-pointer-awareness
- **可用内容**: - **URL**: https://ehs.dartmouth.edu/laboratory-research-safety/laser-safety-program/laser-pointer-awareness
- **适用文章**: 
- **使用次数**: 0


## [引用简述]
- **来源 URL**: https://pmc.ncbi.nlm.nih.gov/articles/PMC7005768/
- **可用内容**: - **URL**: https://pmc.ncbi.nlm.nih.gov/articles/PMC7005768/
- **适用文章**: 
- **使用次数**: 0

## [引用简述]
- **来源 URL**: https://www.fda.gov/radiation-emitting-products/alerts-and-notices/illuminating-facts-about-laser-pointers)
- **可用内容**: - Source: [FDA](https://www.fda.gov/radiation-emitting-products/alerts-and-notices/illuminating-facts-about-laser-pointers) | [eCFR](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-J/part-1040/section-1040.10)
- **适用文章**: laser, pointer, build, quality, high
- **使用次数**: 2


## [引用简述]
- **来源 URL**: https://www.nist.gov/news-events/news/2013/03/nist-tests-underscore-potential-hazards-green-laser-pointers)
- **可用内容**: - Source: [NIST News](https://www.nist.gov/news-events/news/2013/03/nist-tests-underscore-potential-hazards-green-laser-pointers) | [PDF](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=912603)
- **适用文章**: laser, pointer, build, quality, high
- **使用次数**: 3


## [引用简述]
- **来源 URL**: https://arxiv.org/pdf/1008.1452)
- **可用内容**: - Source: [arXiv](https://arxiv.org/pdf/1008.1452)
- **适用文章**: laser, pointer, build, quality, high
- **使用次数**: 1


## [引用简述]
- **来源 URL**: https://www.osha.gov/laser-hazards/standards)
- **可用内容**: - Source: [OSHA Standards](https://www.osha.gov/laser-hazards/standards) | [LIA](https://www.lia.org/resources/laser-safety-information/laser-safety-standards)
- **适用文章**: laser, pointer, build, quality, high
- **使用次数**: 3


## [引用简述]
- **来源 URL**: https://www.ehs.iastate.edu/laser-pointer-safety)
- **可用内容**: - Source: [Iowa State EHS](https://www.ehs.iastate.edu/laser-pointer-safety)
- **适用文章**: laser, pointer, build, quality, high
- **使用次数**: 1

## [引用简述]
- **来源 URL**: https://www.faa.gov/hazmat/packsafe/lithium-batteries
- **可用内容**: - 来源: https://www.faa.gov/hazmat/packsafe/lithium-batteries
- **适用文章**: laser, pointer, battery, guide, 18650
- **使用次数**: 1


## [引用简述]
- **来源 URL**: https://www.tsa.gov/travel/security-screening/whatcanibring/all?combine=batteries&page=1
- **可用内容**: - 来源: https://www.tsa.gov/travel/security-screening/whatcanibring/all?combine=batteries&page=1
- **适用文章**: laser, pointer, battery, guide, 18650
- **使用次数**: 1


## [引用简述]
- **来源 URL**: https://research.columbia.edu/lithium-ion-battery-safety
- **可用内容**: - 来源: https://research.columbia.edu/lithium-ion-battery-safety
- **适用文章**: laser, pointer, battery, guide, 18650
- **使用次数**: 1


## [引用简述]
- **来源 URL**: https://www.nrel.gov/transportation/energy-storage-safety.html
- **可用内容**: - 来源: https://www.nrel.gov/transportation/energy-storage-safety.html
- **适用文章**: laser, pointer, battery, guide, 18650
- **使用次数**: 1


## [引用简述]
- **来源 URL**: https://www.ul.com/insights/enhance-workplace-lithium-ion-battery-safety
- **可用内容**: - 来源: https://www.ul.com/insights/enhance-workplace-lithium-ion-battery-safety
- **适用文章**: laser, pointer, battery, guide, 18650
- **使用次数**: 1

## 2) MKS / ILX Lightwave — 激光二极管寿命加速老化模型
- **来源**: [api.p1.mks.com](https://api.p1.mks.com/medias/sys_master/images/images/hbc/h43/8797050241054/AN33-Estimating-Laser-Diode-Lifetimes-and-Activation-Energy.pdf)
- **关键发现**: [see source]
- **类型**: 
- **适用文章**: 
- **标签**: 
- **使用次数**: 0


## 3) RP Photonics — 532nm DPSS vs 520nm direct-diode 可靠性对比
- **来源**: [rp-photonics.com](https://www.rp-photonics.com/bg/buy_laser_pointers.html)
- **关键发现**: [see source]
- **类型**: 
- **适用文章**: 
- **标签**: 
- **使用次数**: 0


## 4) FDA — 激光笔安全与 flash blindness
- **来源**: [fda.gov](https://www.fda.gov/radiation-emitting-products/alerts-and-notices/illuminating-facts-about-laser-pointers)
- **关键发现**: [see source]
- **类型**: 
- **适用文章**: 
- **标签**: safety, legal
- **使用次数**: 2


## 5) Harvard Astronomy — 观星用激光笔低温实测
- **来源**: [hea-www.harvard.edu](https://hea-www.harvard.edu/~fine/opinions/z-bolt-6plus.html)
- **关键发现**: [see source]
- **类型**: 
- **适用文章**: 
- **标签**: astronomy
- **使用次数**: 0

## OSHA: Li-ion battery thermal runaway identification
- **来源**: [OSHA Lithium Battery Safety](https://www.osha.gov/sites/default/files/publications/OSHA4480.pdf)
- **关键发现**: Thermal runaway can be triggered by improper charging; warning signs include battery heating, gas release, vapor/smoke, and fire. Spare lithium-ion batteries must be carry-on only; 100Wh per battery limit for consumer devices; overcharging can trigger thermal runaway. Do not buy or use loose 18650 c
- **类型**: .gov federal safety agency .gov aviation safety regulation .gov consumer product safety warning Industry standard and certification body .gov federal regulatory agency .edu university safety guideline .edu university safety guideline
- **适用文章**: 
- **标签**: battery, charger, case, safety
- **使用次数**: 0


## Source: [CPSC 18650 Warning](https://www.cpsc.gov/Newsroom/News-Releases/2021/CPSC-Issues-Consumer-S
- **来源**: [cpsc.gov](https://www.cpsc.gov/Newsroom/News-Releases/2021/CPSC-Issues-Consumer-Safety-Warning-Serious-Injury-or-Death-Can-Occur-if-Lithium-Ion-Battery-Cells-Are-Separated-from-Battery-Packs-and-Used-to-Power-Devices))
- **关键发现**: Source: [CPSC 18650 Warning](https://www.cpsc.gov/Newsroom/News-Releases/2021/CPSC-Issues-Consumer-S
- **类型**: 
- **适用文章**: 
- **标签**: battery, safety, power
- **使用次数**: 0


## Source: [UL Battery Safety Testing](https://www.ul.com/services/battery-safety-testing)
- **来源**: [ul.com](https://www.ul.com/services/battery-safety-testing))
- **关键发现**: Source: [UL Battery Safety Testing](https://www.ul.com/services/battery-safety-testing)
- **类型**: 
- **适用文章**: 
- **标签**: safety, battery, support
- **使用次数**: 0


## Source: [FDA Laser Guide](https://www.fda.gov/media/74026/download)
- **来源**: [fda.gov](https://www.fda.gov/media/74026/download))
- **关键发现**: Source: [FDA Laser Guide](https://www.fda.gov/media/74026/download)
- **类型**: 
- **适用文章**: 
- **标签**: safety, legal
- **使用次数**: 0


## Source: [MIT Lithium Battery Safety](https://ehs.mit.edu/wp-content/uploads/2019/09/Lithium_Battery_
- **来源**: [ehs.mit.edu](https://ehs.mit.edu/wp-content/uploads/2019/09/Lithium_Battery_Safety_Guidance.pdf))
- **关键发现**: Source: [MIT Lithium Battery Safety](https://ehs.mit.edu/wp-content/uploads/2019/09/Lithium_Battery_
- **类型**: 
- **适用文章**: 
- **标签**: battery, safety
- **使用次数**: 0


## Source: [CMU Li-ion Safety](https://www.cmu.edu/ehs/Laboratory-Safety/chemical-safety/documents/ehs-
- **来源**: [cmu.edu](https://www.cmu.edu/ehs/Laboratory-Safety/chemical-safety/documents/ehs-guideline---lithium-ion-battery-safety.pdf))
- **关键发现**: Source: [CMU Li-ion Safety](https://www.cmu.edu/ehs/Laboratory-Safety/chemical-safety/documents/ehs-
- **类型**: 
- **适用文章**: 
- **标签**: battery, safety
- **使用次数**: 0

## CPSC 18650 safety warning: loose cells and improper chargers
- **来源**: [CPSC 18650 Warning](https://www.cpsc.gov/Newsroom/News-Releases/2021/CPSC-Issues-Consumer-Safety-Warning-Serious-Injury-or-Death-Can-Occur-if-Lithium-Ion-Battery-Cells-Are-Separated-from-Battery-Packs-and-Used-to-Power-Devices)
- **关键发现**: Do not buy or use loose 18650 cells; improper chargers can cause charging beyond the cells specifications, leading to thermal runaway, fire, and explosion.
- **类型**: .gov consumer product safety warning
- **适用文章**: 
- **标签**: battery, safety, charger, quality
- **使用次数**: 0


## MIT EHS: Lithium battery storage and charging safety
- **来源**: [MIT Lithium Battery Safety](https://ehs.mit.edu/wp-content/uploads/2019/09/Lithium_Battery_Safety_Guidance.pdf)
- **关键发现**: Recommend long-term storage at approximately 3.8V; never leave lithium batteries charging unattended.
- **类型**: .edu university safety guideline
- **适用文章**: 
- **标签**: battery, safety, charger, case
- **使用次数**: 0


## CMU EHS: Lithium-ion battery charging precautions
- **来源**: [CMU Li-ion Safety](https://www.cmu.edu/ehs/Laboratory-Safety/chemical-safety/documents/ehs-guideline---lithium-ion-battery-safety.pdf)
- **关键发现**: Do not charge on combustible surfaces; do not leave charging unattended; avoid metal short circuits during transport and storage.
- **类型**: .edu university safety guideline
- **适用文章**: 
- **标签**: battery, charger, case
- **使用次数**: 0

## IEC 60529 IP Code — IP67 = dust-tight + 1m immersion 30 minutes
- **来源**: [IEC IP Ratings](https://www.iec.ch/ip-ratings)
- **关键发现**: IP67 = complete dust protection (6) + immersion in 1m water for 30 minutes without ingress (7). IP65 = dust-tight + low-pressure water jets only.
- **类型**: International Standard / IEC
- **适用文章**: `laser, pointer, case`, `laser, pointer
- **标签**: 
- **使用次数**: 0


## MIL-STD-810G Method 516.6 Procedure IV — Transit Drop Test
- **来源**: [MIL-STD-810G Shock PDF](http://www.vibrationdata.com/tutorials/MIL810G_shock.pdf)
- **关键发现**: Equipment under 45.4 kg must survive 26 drops from 122 cm onto 2-inch plywood over concrete (6 faces, 12 edges, 8 corners).
- **类型**: Military Standard / US DoD
- **适用文章**: `laser, pointer, case`, `laser, pointer
- **标签**: `safety`
- **使用次数**: 0

## FAA 2025 Laser Incident Statistics — 10,993 strikes, $32,646 per violation
- **来源**: [FAA Laser Incidents](https://www.faa.gov/about/initiatives/lasers)
- **关键发现**: 2025: 10,993 laser-aircraft strikes. Civil penalty up to $32,646 per violation. Down 14% from 2024.
- **类型**: .gov / Federal Aviation Administration
- **适用文章**: `laser, pointer, mount`, `laser, pointer
- **标签**: `price` `legal`
- **使用次数**: 0


## PubMed peer-reviewed: minimum laser power for astronomy pointing — 2.38 mW average sufficient
- **来源**: [PubMed / Optometry and Vision Science](https://pubmed.ncbi.nlm.nih.gov/20035242/)
- **关键发现**: 23 observers in light-polluted urban conditions; global average 2.38 mW; 22/23 chose 1.37-3.53 mW. <5mW sufficient for educational nighttime astronomy activities.
- **类型**: Peer-reviewed academic journal
- **适用文章**: `laser, pointer, mount`, `laser, pointer
- **标签**: `power` `astronomy`
- **使用次数**: 0


## RASC Green Laser Pointer Brochure — 5mW sufficient dark sky, 10km airport restriction, >30mW avoid
- **来源**: [RASC GLP Brochure](https://www.rasc.ca/sites/default/files/GLP_Brochure_v.3_copy_2.pdf)
- **关键发现**: 5 mW sufficient in dark sky; no use within 10 km of airport; >30 mW should be avoided.
- **类型**: Official astronomy society guidance (Royal Astronomical Society of Canada)
- **适用文章**: `laser, pointer, mount`, `laser, pointer
- **标签**: `travel` `power` `legal`
- **使用次数**: 0


## Astronomical League Laser Pointer Guidance — constant-on interferes with astrophotography
- **来源**: [Astronomical League](https://www.astroleague.org/files/u220/Astro%20Note%20A2%20-%20Laser%20Pointers.pdf)
- **关键发现**: 5mW green adequate for solo/small group; 10-25mW for large groups/high light pollution; constant-on lasers interfere with astrophotography.
- **类型**: Official astronomy organization guidance (largest US amateur astronomy org)
- **适用文章**: `laser, pointer, mount`, `laser, pointer
- **标签**: 
- **使用次数**: 0

## Coherent: Optical lens cleaning protocol — dry cleaning first, spectroscopic-grade IPA/methanol wet cleaning only if dry fails, figure-8 motion with optical-grade lens paper
- **来源**: [Coherent Laser Manual](https://www.coherent.com/resources/manuals/lasers/surelock-rousb-laser-diode-module-manual.pdf)
- **关键发现**: Industry-standard protocol: always try dry cleaning (compressed air/blower) first. Wet cleaning only when dry insufficient, using spectroscopic-grade IPA or methanol with optical-grade lens paper in gentle figure-8 motion. Never scrub or use circular rubbing.
- **类型**: manufacturer manual / industry standard
- **适用文章**: laser, pointer, cleaning, and, maintenance
- **标签**: `beam optics` `quality`
- **使用次数**: 0


## Laser Components: Blow off particles first — eyeglass cloths contain anti-fog substances that damage optical coatings; "Never Clean Metal Coatings"
- **来源**: [Laser Components Optics Cleaning Guide](https://www.lasercomponents.com/en/photonics-portal/knowledge-center/expert-tips/optics-how-to-clean-laser-optics/)
- **关键发现**: Always remove loose particles with compressed air/blower before any wiping (dragging grit = guaranteed scratches). Standard eyeglass cleaning cloths may contain anti-fog chemicals that attack optical coatings. Never apply solvents to metal-coated optics. Only pure acetone or isopropanol recommended 
- **类型**: industry technical article / optics cleaning guide
- **适用文章**: laser, pointer, cleaning, and, maintenance
- **标签**: `safety`
- **使用次数**: 0


## Laser Components: 520nm direct diode operates from -20°C to +60°C vs 532nm DPSS typical -5°C to +50°C — temperature stability and power consistency favor direct diode
- **来源**: [Laser Components 520nm vs 532nm Comparison](https://www.lasercomponents.com/us/photonics-portal/knowledge-center/technical-articles/our-experts-have-done-a-comparison-520-nm-laser-diodes-and-532-nm-dpss-lasers/)
- **关键发现**: 520nm direct diode lasers have a significantly wider operating temperature range (-20°C to +60°C) versus 532nm DPSS lasers (-5°C to +50°C). Direct diode also shows better power stability and superior thermal modulation capability. This explains why 532nm DPSS pointers commonly fail or dim in cold we
- **类型**: industry technical article / wavelength comparison
- **适用文章**: laser, pointer, cleaning, and, maintenance
- **标签**: `outdoor` `beam optics` `quality` `power`
- **使用次数**: 1

## NIST measured 20 mW of invisible IR leakage from one commercial green laser pointer
- **来源**: [NIST TN 906138 — A Green Laser Pointer Hazard](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=906138)
- **关键发现**: One inexpensive green DPSS pointer emitted 20 mW of invisible 1064 nm IR during normal use. Low DPSS conversion efficiency leaves intracavity IR power high, venting through aperture when IR filter is missing. Invisible IR does not trigger blink reflex, allowing retinal damage without user awareness.
- **类型**: .gov
- **适用文章**: 
- **标签**: `safety` `power` `price`
- **使用次数**: 0


## ANSI Z136.1 defines Maximum Permissible Exposure levels and laser safety controls
- **来源**: [Laser Institute of America — ANSI Z136 standards](https://www.lia.org/)
- **关键发现**: ANSI Z136.1 defines MPE levels and laser safety controls; lasers exceeding Class 3R limits "may be hazardous and should be subject to more rigorous controls such as training, to prevent injury." Referenced by OSHA and FDA as the de facto U.S. occupational laser safety standard.
- **类型**: industry standard
- **适用文章**: 
- **标签**: `safety` `legal`
- **使用次数**: 0


## FAA testing shows 5 mW green pointer can cause retinal burns at 14m, flash-blindness at 350m, aborted landings at 3km
- **来源**: [Sky & Telescope — Some Pointers on Using Laser Pointers](https://skyandtelescope.org/stargazing-and-observing/some-pointers-on-the-use-of-laser-pointers/)
- **关键发现**: A 5 mW green laser pointer can cause retinal burns up to 14 m (50 ft), flash-blindness/afterimages/glare up to 350 m (~1/4 mile), and enough vision interference to prompt aborted landings at 3 km (~2 miles). Based on 2004 FAA testing on volunteers.
- **类型**: .gov
- **适用文章**: 
- **标签**: `safety` `power` `legal`
- **使用次数**: 0


## Health Physics Society: Class 3a pointers hazardous if viewed briefly; 532nm green appears ~5× brighter than 633nm red at equal power
- **来源**: [Health Physics Society — Can I Safely Use a Laser Pointer?](https://hps.org/ate_faq/laser/)
- **关键发现**: Class 3a (1-5 mW) pointers are hazardous if viewed even briefly. The eye perceives 532 nm green light as ~5× brighter than 633 nm red at equal power due to photopic response curve. In California, aiming at a person is a criminal misdemeanor; aiming at an aircraft is a felony.
- **类型**: scientific society
- **适用文章**: 
- **标签**: `power` `safety`
- **使用次数**: 0

## FDA: Check certification labels including 21 CFR 1040.10/1040.11, manufacturer, production date
- **来源**: [FDA - Consumer Safety Alert: Internet Sales of Laser Products](https://www.fda.gov/radiation-emitting-products/alerts-and-notices/consumer-safety-alert-internet-sales-laser-products)
- **关键发现**: 对消费者最有操作性的官方检查项——核对 certification/identification labels
- **类型**: .gov / FDA
- **适用文章**: 
- **标签**: `safety` `legal`
- **使用次数**: 0


## AIP Journal: Near 90% of green, 44% of red laser pointers non-compliant in random testing of 122 units; highest measured 66.5mW
- **来源**: [Journal of Laser Applications - Random Testing Reveals Excessive Power](https://pubs.aip.org/lia/jla/article/25/3/032007/350823/Random-testing-reveals-excessive-power-in)
- **关键发现**: 学术界最常被引用的激光笔超标数据——122 支随机样本中近 90% 绿色不合规
- **类型**: Academic journal / AIP
- **适用文章**: 
- **标签**: 
- **使用次数**: 0

## FDA Import Alert 95-04 — DWPE for Overpower Lasers
- **来源**: [FDA accessdata](https://www.accessdata.fda.gov/cms_ia/importalert_254.html)
- **关键发现**: Overpower (>5mW) laser pointers, laser sights, levels, keychains may be detained without physical examination. Red list updated Feb 2026.
- **类型**: .gov
- **适用文章**: laser, pointer, buying, guide, shipping
- **标签**: `safety` `power` `legal` `shipping`
- **使用次数**: 0


## TSA — Laser Pointers on Planes
- **来源**: [TSA.gov](https://www.tsa.gov/travel/security-screening/whatcanibring/items/laser-pointers)
- **关键发现**: Laser pointers allowed in carry-on and checked luggage. Final decision at TSA officer discretion. Last updated 2018-04-24.
- **类型**: .gov
- **适用文章**: laser, pointer, buying, guide, shipping
- **标签**: `travel` `case`
- **使用次数**: 0


## IATA Lithium Battery Shipping Regulations
- **来源**: [IATA.org](https://www.iata.org/en/youandiata/travelers/batteries/
https://www.faa.gov/hazmat/packsafe/lithium-batteries https://www.tsa.gov/travel/security-screening/whatcanibring/all?combine=batteries&page=1)
- **关键发现**: >100Wh needs airline approval, >160Wh forbidden on passenger aircraft. 18650 (~12Wh) allowed but must be carry-on, not checked.
- **类型**: Industry standard
- **适用文章**: laser, pointer, buying, guide, shipping
- **标签**: `battery` `travel` `case` `legal`
- **使用次数**: 0

## FAA — Laser strike aircraft data
- **来源**: [FAA](https://www.faa.gov/newsroom/laser-strikes-aircraft-drop-second-year-row)
- **关键发现**: 2025 年 10,994 起 aircraft laser strike 报告；337 起伤害；单次最高 $11,000 FAA 罚款
- **类型**: .gov
- **适用文章**: best, small, laser, pointer, for
- **标签**: `price` `legal`
- **使用次数**: 0

## NIST 2013 compliance survey of 122 laser pointers
- **来源**: [Wikipedia](https://en.wikipedia.org/wiki/Laser_pointer)
- **关键发现**: ~50% ≥2× labeled power; max 66.5 mW; >75% IR over limit; 90% green & 44% red failed compliance.
- **类型**: .gov (NIST)
- **适用文章**: 
- **标签**: `power` `quality` `legal`
- **使用次数**: 0

## ILDA — Camera sensor damage thresholds
- **来源**: [ilda.com](https://www.ilda.com/camera-sensor-damage.htm)
- **关键发现**: CMOS damaged at 40,000-60,000 W/cm² (0.25s). CCD damaged at ~9,000 W/cm² (5-10s). Avoid direct laser entry within 1-2 inches of lens.
- **类型**: Industry standard / academic
- **适用文章**: 
- **标签**: `beam optics` `safety`
- **使用次数**: 0

## 18 U.S.C. §39A — Laser Emergency Signaling Exception
- **来源**: [U.S. Code](https://uscode.house.gov/view.xhtml?req=(title%3A18%20section%3A39A%20edition%3Aprelim)
- **关键发现**: Aiming laser at aircraft generally illegal; narrow exception for "individual using a laser emergency signaling device to send an emergency distress signal"
- **类型**: .gov
- **适用文章**: 
- **标签**: `legal`
- **使用次数**: 0


## Laser Pointer Maculopathy in Children (PMC)
- **来源**: [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10313552/)
- **关键发现**: 8 children (11 eyes) confirmed with laser pointer maculopathy; 62.5% occurred 2019-2020; one device measured ~5.8mW
- **类型**: Academic
- **适用文章**: 
- **标签**: 
- **使用次数**: 0

## ICNIRP: Infrared defined as 780nm–1mm; IR-A = 780nm–1.4µm; 1064nm is near-infrared
- **来源**: [icnirp.org](https://www.icnirp.org/en/frequencies/infrared/index.html)
- **关键发现**: 1064nm 明确属于 IR-A / near-infrared，不是 far infrared。
- **类型**: International official body / ICNIRP
- **适用文章**: infrared, laser, pointer, 1064nm, invisible
- **标签**: `safety`
- **使用次数**: 0

## OSHA laser hazard standards
- **来源**: [OSHA](http://www.osha.gov/laser-hazards/standards)
- **关键发现**: OSHA 标准页列出 29 CFR 1910.132（General requirements）与 1910.133（Eye and face protection），以及 ANSI Z136.6（Safe Use of Lasers Outdoors）和 Z136.9（Safe Use of Lasers in Manufacturing Environments）。
- **类型**: .gov
- **适用文章**: laser, pointer, for, construction, work
- **标签**: `safety` `outdoor`
- **使用次数**: 0


## Berkeley Lab laser pointer advisory
- **来源**: [Berkeley Lab](https://ehs.lbl.gov/resource/laser-pointer-advisory-for-use-at-lbnl/)
- **关键发现**: 允许的 laser pointers 为可见光 Class 1/2/3a(3R) 最高 5mW；"Do not use Class 3B or Class 4 lasers as laser pointers, as these are illegal in the United States."
- **类型**: .gov (国家实验室)
- **适用文章**: laser, pointer, for, construction, work
- **标签**: `safety` `legal`
- **使用次数**: 0


## OSHA hazard classification
- **来源**: [OSHA](http://www.osha.gov/laser-hazards/hazards)
- **关键发现**: Class 3B = Immediate skin/eye hazard from direct beam. Class 4 = Immediate skin/eye hazard from direct or reflected beam; may present fire hazard.
- **类型**: .gov
- **适用文章**: laser, pointer, for, construction, work
- **标签**: `safety` `beam optics`
- **使用次数**: 0

## 激光照射飞行器 FAA 统计 (1996-1999)
- **来源**: [FAA tech report 0107.pdf](https://www.faa.gov/sites/faa.gov/files/data_research/research/med_humanfacs/oamtechreports/0107.pdf)
- **关键发现**: 1996.1–1999.7，FAA Western-Pacific Region 一个区就记录了 150+ 起 low-flying aircraft 被手持 laser 照射事件，多起导致飞行员视觉损伤。
- **类型**: .gov
- **适用文章**: laser, pointer, night, vision, low
- **标签**: `legal`
- **使用次数**: 0


## 激光与航空安全 — 闪光盲距离量化
- **来源**: [Wikipedia - Lasers and aviation safety](https://en.wikipedia.org/wiki/Lasers_and_aviation_safety)
- **关键发现**: 5mW 合法 laser pointer 在 350 ft (110 m) 距离即可造成 flash blindness（光照水平 50 W/cm²）；6W green 532nm laser 可在 8,200 ft (2.5 km) 外造成 flash blindness。
- **类型**: 学术/行业（引用 FAA/ICAO 原始数据）
- **适用文章**: laser, pointer, night, vision, low
- **标签**: `safety`
- **使用次数**: 0


## RP Photonics — 激光光束在大气中的可见度
- **来源**: [RP Photonics](https://www.rp-photonics.com/spotlight_2010_01_11.html)
- **关键发现**: 532nm 与 670nm 的 Rayleigh scattering 差异为 2.6×；photopic 下绿/红敏感度比约 20×，scotopic（暗适应）下该比值显著更高。
- **类型**: 行业标准百科
- **适用文章**: laser, pointer, night, vision, low
- **标签**: 
- **使用次数**: 0


## NPS — 人眼暗适应与红光价值
- **来源**: [NPS - Dark Adaptation](https://www.nps.gov/articles/dark-adaptation-of-the-human-eye-and-the-value-of-red-flashlights.htm)
- **关键发现**: 红光在低强度下不会显著漂白 rhodopsin（视紫红质），因此能保护暗适应；亮红光同样会破坏暗适应。
- **类型**: .gov
- **适用文章**: laser, pointer, night, vision, low
- **标签**: 
- **使用次数**: 0

## USCG 飞行规则：激光射入 cockpit → 强制任务中止
- **来源**: [USCG News Release](https://www.news.uscg.mil/Press-Releases/Article/3261541/coast-guard-seeks-public-information-after-laser-strike-hits-coast-guard-cutter/)
- **关键发现**: "Coast Guard flight rules dictate that the aircraft must abort its mission"
- **类型**: 美国海岸警卫队官方 (.mil)
- **适用文章**: laser, pointer, boating, maritime, laser
- **标签**: `travel`
- **使用次数**: 0


## USCG 工程处 VDS 认证标准：laser 不在 46 CFR 160/161 任何批准清单
- **来源**: [USCG CG-ENG-4](https://www.dco.uscg.mil/CG-ENG-4/VDS/)
- **关键发现**: RTCM SC13200.0 为 eVDSD 唯一认可标准；2026-04 启动 pyrotechnic → eVDSD 转型
- **类型**: USCG 工程处 (.mil)
- **适用文章**: laser, pointer, boating, maritime, laser
- **标签**: 
- **使用次数**: 0


## COLREG Rule 36 + RMI Advisory
- **来源**: [SAFETY4SEA](https://safety4sea.com/rmi-informs-about-two-incidents-with-laser-pointers/)
- **关键发现**: "the use of high intensity intermittent lights... shall be avoided"
- **类型**: IMO 公约 + 旗国官方
- **适用文章**: laser, pointer, boating, maritime, laser
- **标签**: 
- **使用次数**: 0


## CCA Safety at Sea + USCG LT Gorgol 官方回复
- **来源**: [CCA SAS](https://sas.cruisingclub.org/node/236)
- **关键发现**: USCG 不认为 laser 是 COLREGS 定义的 rescue signal；至多为 "signal to attract attention"
- **类型**: 安全委员会 + USCG 官员书面回复
- **适用文章**: laser, pointer, boating, maritime, laser
- **标签**: `safety`
- **使用次数**: 0
