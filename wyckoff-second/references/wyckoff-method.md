# 威科夫二世方法参考

This reference combines the charting workflow with the book-derived method from 《威科夫操盘法》 by 孟洪涛 / Edward Meng. Use it as the operating rulebook for CSV-based chart judgment.

## Reading Order

Always read in this order:

1. **背景**: markup, markdown, accumulation, distribution, re-accumulation, re-distribution, or unclear.
2. **价量形态**: candle/range size, volume height, speed, support/resistance location, MA context.
3. **行为性质**: demand absorption, supply expansion, no demand, no supply, stopping action, test, trap, or follow-through.
4. **CM意图**: whether large capital is absorbing public supply, distributing into public demand, testing supply, or withdrawing support.
5. **行动/风险**: wait, mark, enter scenario, exit scenario, or invalidate.

Do not name a pattern first. Pattern names are conclusions from behavior.

## Three Laws From the Book

- **供求关系**: demand greater than supply creates upward trend; supply greater than demand creates downward trend.
- **因果关系**: durable trends need preparation. Accumulation creates the cause for markup; distribution creates the cause for markdown.
- **努力与结果**: volume is effort; price progress is result. Big effort with poor result implies absorption or hidden opposition.

## Accumulation Rules

Use accumulation logic when markdown slows and price begins building a base.

Required evidence:

- SC or stopping behavior: wide spread/high volume selling is absorbed by demand.
- AR: reaction after panic helps define the upper range, but is not a buy signal.
- ST: retest near the low with smaller spread/volume shows supply weakening.
- Phase B: range builds cause; rallies should become stronger than declines, or supply should dry up.
- Right-side proof: Spring, terminal shakeout, SOS/JOC, or LPS.

Entry-quality hierarchy:

1. Low-volume test after SOS/JOC.
2. Spring with low supply or Spring followed by successful ST.
3. Terminal shakeout with rapid recovery and later low-supply test.
4. LPS above key danger points.

Invalidation:

- Price loses range support on expanding volume and then rallies with no demand.
- Spring rebound fails and SOW appears.
- Post-entry behavior lacks higher high/higher low/higher close.

## Distribution Rules

Use distribution logic when markup becomes extended and public demand is emotional.

Required evidence:

- Initial supply: high-volume rejection or decline that changes the bullish rhythm.
- BC: buying climax; public demand expands while CM can sell into it.
- Natural reaction and ST: define the range and test whether demand is exhausted.
- UT/UTAD or failed breakout: price tests demand above resistance and fails.
- SOW/break of ice: supply breaks protected support.
- LPSY: weak rally after SOW cannot reclaim the ice/range.

Invalidation:

- Demand reclaims the ice/range with wide spread and follow-through.
- A supposed UT holds above resistance and then passes a low-volume test.
- Supply bar has no bearish follow-through and is absorbed immediately.

## Event Validation Table

| Event | Valid If | Invalid / Warning |
|---|---|---|
| Spring | Break below support returns quickly; supply absent or absorbed; bullish follow-through | Bearish background, no-demand rebound, high-volume down follow-through |
| Terminal Shakeout | Deep range break recovers fast and later tests show no supply | Recovery weak, retest volume remains large, support lost again |
| JOC/SOS | Demand pushes through resistance and pullback is low-volume/small-spread | Breakout falls back into range on high volume |
| UT/UTAD | Break above resistance fails back into range and supply follows | Demand absorbs supply and holds above resistance |
| SOW | Wide downside progress or ice break with supply; weak rally confirms | Rally reclaims support/ice with demand |
| LPSY | Rally after SOW is weak and cannot recover key level | Rally expands and absorbs supply |
| SOT | New highs/lows make less progress; stopping behavior appears near key zone | Later demand/supply resolves with strong follow-through opposite the warning |

## Support and Resistance

Support is not a line; it exists only if demand absorbs supply at that price. Resistance is not a line; it exists only if supply overwhelms demand. When price revisits a level, ask:

- Is volume expanding or shrinking?
- Does price make expected progress?
- Is there follow-through?
- Is this test on the right side of a range or in random middle noise?

## Range Drawing

- Accumulation range height: use dense Phase B closing-price band, excluding SC tail and AR extreme.
- Distribution range height: use dense Phase B closing-price band, excluding BC/UT emotional extremes.
- Start accumulation at SC/stopping behavior; end at valid SOS/JOC, or latest bar if unfinished.
- Start distribution at BC/initial supply; end at SOW/break of ice, or latest bar if unfinished.
- Do not force Phase A-E. Mark only phases supported by visible events.

## CSV and Chart Expectations

- Normalize dates and sort ascending.
- Compute MA50 and MA200 after parsing.
- Use the latest 500 rows by default unless the user asks otherwise.
- If volume is missing/unreliable, lower confidence in SC/BC/SOS/SOW/effort-result claims.
- Main chart must include close, MA50, MA200, Chinese event labels, shaded range boxes, and phase dividers.
- Use volume subplot when available.
- Chinese labels should be concise: `[术语]：价量理由`.
- Longer reasoning belongs in the final response, not crowded over price action.

## Manual Review Rules

The script is a first-pass detector. Override it when:

- The selected range ignores a clearer recent range.
- SC/BC is placed on an emotional tail but the actual cause-building range starts later.
- The detector marks accumulation in an obvious supply-controlled markdown.
- The detector marks distribution in an obvious demand-controlled markup.
- Event order violates the book logic, such as LPS before SOS/JOC or LPSY before SOW.

Before final reply, verify:

- CSV parsed and date order is ascending.
- Output image exists and Chinese text renders.
- MA50/MA200 are present.
- Key events are on visible price zones.
- `book_judgment.invalidation` is included in the response.
