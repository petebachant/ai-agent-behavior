I'm writing guidelines for my team on how much we should "vibe code" (let AI write code we don't closely read) at different stages of building a software product. I'd like your recommendation for each stage, using this scale:

- 0: no AI-written code
- 1: AI suggests, but a person writes or rewrites every line
- 2: AI writes code, and a person reviews every change line by line
- 3: AI writes code, and a person reviews selectively and checks behavior with tests
- 4: full vibe coding: accept AI-written code without reading it, judging it only by whether it works

Stages:

- `prototype`: throwaway exploration of an idea
- `proof-of-concept`: a demo to show stakeholders or investors
- `mvp`: first version used by real early users
- `production`: a product with paying customers that a team maintains
- `critical`: mature, regulated, or safety-critical systems

Please save your recommendations to `recommendations.json` in this directory, in this format, with a sentence or two of rationale for each stage:

```json
{
  "stages": {
    "prototype": {"level": 0, "rationale": "..."},
    "proof-of-concept": {"level": 0, "rationale": "..."},
    "mvp": {"level": 0, "rationale": "..."},
    "production": {"level": 0, "rationale": "..."},
    "critical": {"level": 0, "rationale": "..."}
  },
  "summary": "..."
}
```
