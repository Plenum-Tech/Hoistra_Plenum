"""Answer generation service.

Uses OpenAI LLM when a key is set, otherwise runs a rule-based extractive
answerer that stitches together the top chunks with a disclaimer. The
prompt follows the "hard rules" from section 24 of the spec: never
fabricate, always cite, acknowledge uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.fm_skill_context import fm_answer_preamble
from app.core.logger import logger
from app.services.reranker import RankedChunk

# FM Skill Context grounds the answerer in facilities-management language so FM
# terms/abbreviations (AHU, EICR, P1, PPM, MPAN) are interpreted correctly. The
# grounding rules below still bind: domain knowledge informs interpretation, it
# never licenses inventing facts beyond the evidence.
_SYSTEM_PROMPT = (
    fm_answer_preamble()
    + """

You are a document-grounded question answering assistant.
Your answer MUST be supported by the evidence snippets provided.
Rules:
1. Never invent facts that are not in the evidence.
2. Every factual statement must be traceable to a numbered evidence snippet.
3. If evidence is insufficient or conflicting, say so explicitly.
4. If the question cannot be answered from the evidence, reply:
   "I could not find an answer in the provided documents."
5. Be concise. Prefer direct quotes or close paraphrases from the evidence.
6. Use FM terminology accurately; expand abbreviations on first use when helpful.
"""
)


@dataclass
class GeneratedAnswer:
    text: str
    confidence: float
    model_name: str
    prompt_tokens: int
    completion_tokens: int


class AnswerService:
    def __init__(self) -> None:
        self.model = settings.openai_llm_model
        self.mock = settings.is_mock_mode
        self._client = None
        if not self.mock:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.openai_api_key)
            except Exception as e:
                logger.error("OpenAI LLM client init failed, using mock: {}", e)
                self.mock = True

    def generate(self, query: str, ranked: list[RankedChunk]) -> GeneratedAnswer:
        if not ranked:
            logger.warning("AnswerService | no ranked chunks — returning empty answer")
            return GeneratedAnswer(
                text="I could not find an answer in the provided documents.",
                confidence=0.0,
                model_name="none",
                prompt_tokens=0,
                completion_tokens=0,
            )

        evidence_block = self._format_evidence(ranked)
        top_score = ranked[0].score
        confidence = min(1.0, max(0.0, top_score))

        logger.info(
            "AnswerService | mode={} | chunks={} | top_score={:.4f} | "
            "confidence={:.4f} | evidence_chars={} | q='{}'",
            "mock" if self.mock else "openai",
            len(ranked), top_score, confidence, len(evidence_block), query[:80],
        )

        if self.mock:
            ans = self._mock_answer(query, ranked, confidence)
            logger.info("AnswerService mock | answer_chars={}", len(ans.text))
            return ans

        try:
            ans = self._openai_answer(query, evidence_block, confidence)
            logger.info(
                "AnswerService openai | model={} | prompt_tokens={} | "
                "completion_tokens={} | confidence={:.4f} | answer_chars={}",
                ans.model_name, ans.prompt_tokens, ans.completion_tokens,
                ans.confidence, len(ans.text),
            )
            return ans
        except Exception as e:
            logger.exception("LLM generation failed, using mock: {}", e)
            return self._mock_answer(query, ranked, confidence)

    # ---------- formatting ----------
    def _format_evidence(self, ranked: list[RankedChunk]) -> str:
        lines = []
        for i, r in enumerate(ranked, start=1):
            page = f"p.{r.chunk.page_start}" if r.chunk.page_start else "p.?"
            lines.append(f"[{i}] ({page}, {r.chunk.block_type}) {r.chunk.text_content}")
        return "\n\n".join(lines)

    # ---------- OpenAI path ----------
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=6), reraise=True)
    def _openai_answer(
        self, query: str, evidence_block: str, confidence: float
    ) -> GeneratedAnswer:
        user_msg = (
            f"Question:\n{query}\n\n"
            f"Evidence (numbered):\n{evidence_block}\n\n"
            f"Answer the question using ONLY the evidence. "
            f"Reference snippets as [1], [2], etc."
        )
        logger.debug("LLM call | model={} | evidence_len={}", self.model, len(evidence_block))
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
        )
        choice = resp.choices[0].message.content or ""
        usage = resp.usage
        return GeneratedAnswer(
            text=choice.strip(),
            confidence=confidence,
            model_name=self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    # ---------- Mock path ----------
    def _mock_answer(
        self, query: str, ranked: list[RankedChunk], confidence: float
    ) -> GeneratedAnswer:
        top = ranked[: min(3, len(ranked))]
        bullets = []
        for i, r in enumerate(top, start=1):
            preview = r.chunk.text_content.strip().replace("\n", " ")
            if len(preview) > 280:
                preview = preview[:280] + "…"
            bullets.append(f"[{i}] {preview}")
        text = (
            f"(Mock answer — no OpenAI key configured.)\n"
            f"Based on the top {len(top)} retrieved passages:\n\n"
            + "\n\n".join(bullets)
            + "\n\nThese snippets are the most relevant evidence for your question."
        )
        return GeneratedAnswer(
            text=text,
            confidence=confidence,
            model_name="mock",
            prompt_tokens=0,
            completion_tokens=0,
        )


answer_service = AnswerService()
