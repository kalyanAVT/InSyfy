import os
from dotenv import load_dotenv
load_dotenv()

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from graph.state import CritiqueResult, SynthesisResult, ResearchPlan
from agents.token_utils import extract_token_count


class CriticAgent:
    """Quality gate: evaluates synthesis and decides whether to proceed or retry."""
    
    def __init__(self):
        provider = os.getenv("LLM_PROVIDER", "groq")
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        
        if provider == "groq":
            self.llm = ChatGroq(
                model=model,
                temperature=0.1,
                api_key=os.getenv("GROQ_API_KEY")
            )
        else:
            self.llm = ChatOpenAI(
                model=model,
                temperature=0.1,
                api_key=os.getenv("OPENAI_API_KEY")
            )
        
        self.parser = PydanticOutputParser(pydantic_object=CritiqueResult)
        self.last_token_usage = 0
    
    def run(self, synthesis: SynthesisResult, plan: ResearchPlan, retry_count: int) -> CritiqueResult:
        findings_text = "\n".join([
            f"- {f.claim} (confidence: {f.confidence:.2f}, chunks: {len(f.supporting_chunks)})"
            for f in synthesis.key_findings
        ])
        
        sub_queries_text = "\n".join([
            f"- {sq.query}" for sq in plan.sub_queries
        ])
        
        prompt = ChatPromptTemplate.from_template("""You are a quality critic agent.

Evaluate the synthesis against the original research plan. Score strictly.

{format_instructions}

Original Question: {question}
Quality Threshold: {threshold}

Sub-queries from plan:
{sub_queries}

Synthesis findings:
{findings}

Overall confidence: {overall_confidence}
Source diversity: {source_diversity}

Evaluate:
1. coverage_score: What fraction of sub-queries are addressed? (0.0-1.0)
2. contradiction_score: Are contradictions resolved? Penalize unresolved. (0.0-1.0, higher = fewer contradictions)
3. source_diversity_score: Are sources varied? Penalize over-reliance on one source. (0.0-1.0)
4. confidence_score: Average confidence of findings. (0.0-1.0)

quality_score = average of the four scores.

gap_analysis: List specific gaps — which sub-queries are unanswered, what additional searches might help.

proceed: True if quality_score >= {threshold}, else False.

Respond with ONLY the JSON. No extra text.""")
        
        self.last_token_usage = 0
        
        try:
            messages = prompt.format_messages(
                question=plan.sub_queries[0].query if plan.sub_queries else "Unknown",
                threshold=plan.quality_threshold,
                sub_queries=sub_queries_text,
                findings=findings_text,
                overall_confidence=synthesis.overall_confidence,
                source_diversity=synthesis.source_diversity_score,
                format_instructions=self.parser.get_format_instructions()
            )
            ai_message = self.llm.invoke(messages)
            self.last_token_usage = extract_token_count(ai_message)
            
            result = self.parser.parse(ai_message.content)
            return result
        except Exception as e:
            return CritiqueResult(
                quality_score=0.6,
                coverage_score=0.6,
                contradiction_score=0.6,
                source_diversity_score=0.6,
                confidence_score=0.6,
                gap_analysis=f"LLM critique failed: {str(e)}. Proceeding with caution.",
                proceed=True
            )