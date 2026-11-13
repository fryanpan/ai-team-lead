---
name: survey-design
description: Use when writing, reviewing or fielding a questionnaire for real respondents (a resident or customer survey, a poll, a before and after survey for a pilot), or when choosing a panel or vendor, a sample size or whether a margin of error applies (Pollfish, Prolific, YouGov, Ipsos, NORC, a local pollster).
---

# Designing a survey

Rules marked "practice" come from reviewing a real survey with its owner, not from published guidance.

## Order

1. **Open questions come before any list.** A list steers people toward its options. Of voters shown a list, 58% picked the economy; of those asked the open question, 35% named it ([Pew, Writing Survey Questions](https://www.pewresearch.org/our-methods/u-s-surveys/writing-survey-questions/)). Example (a city resident survey): safety isn't mentioned until after the two open questions.
2. **Ask general questions before specific ones.** A specific question asked first shifts the answer to the general one after it ([AAPOR, Best Practices](https://aapor.org/standards-and-ethics/best-practices/)).
3. **Within those two rules, the questions you care about most go first.** Put background and habit questions together with the demographics at the end, because people tire and quit as a survey goes on (practice).
4. **Shuffle unordered answer lists for each person**, because people on a screen pick from the top (Pew).

## Wording

5. **Use common, plain words** (practice). Example (same survey): "getting around" read as odd, so the survey says "travel" and "getting places".
6. **Don't define the outcome you are measuring** (for example "safe"). Ask people what they mean by it.
7. **Ask about one thing per question and name both sides** (AAPOR).
8. **Use ratings or a choice between statements, not agree or disagree**; people tend to agree with whatever they're shown (Pew).
9. **Avoid pick-all-that-apply.** People pick fewer of the later items than when asked yes or no for each (Dillman, *Internet, Phone, Mail, and Mixed-Mode Surveys*, 2014).
10. **Borrow closed questions word for word, with their scale**, from any survey you want to compare against. Note the source only on those questions, and don't explain the scale elsewhere (practice).

## Answers

11. **Make "Not sure" and "doesn't apply" separate options**, and let people skip. A non-cyclist answers "I don't bike". AAPOR lists the two separately and warns that forcing an answer makes people quit.
12. **When you ask what people would give up for a change, follow the NOAA panel.** Each person gets one yes or no at one price, with the price varied across randomly assigned groups, an explicit no-answer option, and "Why?" afterward ([NOAA panel report, 1993](https://webarchive.library.unt.edu/web/20130110004518id_/http://www.darrp.noaa.gov/library/pdf/cvblue.pdf)).

## Fielding and quality

13. **Build for a phone first**: two questions a screen at most, no grids, answers listed down the screen ([AAPOR mobile task force, 2014](https://aapor.org/wp-content/uploads/2022/11/REVISED_Mobile_Technology_Report_Final_revised10June14.pdf)).
14. **Write the drop rule before fielding** (practice): one attention check, plus rules for speeders and junk open answers, reporting each count. Soft launch to about 50 and check the median time and the open answers before releasing the rest.
15. **Have two people code a random sample of the open answers**, report how often they agree, and use that set to check any AI coding ([Pew methodology](https://www.pewresearch.org/methods/2026/08/27/bogus-respondents-methodology/)). Publish the coding scheme ([AAPOR disclosure standards](https://aapor.org/standards-and-ethics/disclosure-standards/)).
16. **Pick quota and weighting variables for your population**, confirm who builds the weights, and report the design effect (practice). Weighting doesn't make an opt-in sample representative.

## Choosing a provider

Prices as of November 2026. Recheck before quoting.

| Option | Finest targeting | Margin of error? | Rough cost for 500 |
| --- | --- | --- | --- |
| Opt-in panel (Pollfish, Prolific, Cint, YouGov) | City on some (Pollfish filters by city) | No ([AAPOR](https://aapor.org/wp-content/uploads/2023/01/Margin-of-Sampling-Error-508.pdf)) | $1.5k to $3k in one city on [Pollfish](https://www.pollfish.com/pricing/) (estimate) |
| Probability panel (Ipsos KnowledgePanel, NORC AmeriSpeak, SSRS) | National or state | Yes (address sample; nonresponse still matters) | Quote only; NORC's national omnibus is $1,000 a question ([NORC](https://www.norc.org/services-solutions/amerispeak/offerings/omnibus.html)) |
| Local pollster, listed sample, phone and online | City, several languages | Yes, by convention | $20k to $40k [model knowledge — verify] |
| Recruit yourself (QR codes, door to door, an app) | Any area you can reach | No | Low |

- **Under about $3k**, use an opt-in panel and report "of the N who answered" next to a comparison with the Census.
- **If you need a margin of error for one city**, use a local pollster. Ipsos and NORC each have about 150 to 180 SF members, so under 100 completes (estimate).
- **If neighborhoods matter**, panels can't target them well: no vendor publishes its members per city, and ZIP codes don't follow neighborhood edges. Recruit yourself, and add a small paid panel to reach people you miss. A self-recruited sample leans toward engaged people, so its shares are unreliable, but it can track change if recruitment stays the same [inference — mine].
