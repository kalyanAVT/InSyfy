import os
from typing import List
from dotenv import load_dotenv
load_dotenv()

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from graph.state import ResearchPlan, SubQuery
from agents.token_utils import extract_token_count


class PlannerAgent:
    """Breaks a research question into focused sub-queries."""
    
    def __init__(self):
        provider = os.getenv("LLM_PROVIDER", "groq")
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        
        if provider == "groq":
            self.llm = ChatGroq(
                model=model,
                temperature=0.3,
                api_key=os.getenv("GROQ_API_KEY")
            )
        else:
            self.llm = ChatOpenAI(
                model=model,
                temperature=0.3,
                api_key=os.getenv("OPENAI_API_KEY")
            )
        
        self.parser = PydanticOutputParser(pydantic_object=ResearchPlan)
        self.last_token_usage = 0
    
    def run(self, question: str, gap_analysis: str = "") -> ResearchPlan:
        prompt_text = """You are a research planning agent. Your job is to break a research question into 3-5 focused sub-queries.

Given a research question, produce a structured plan with:
1. Sub-queries: Each should be specific, non-overlapping, and cover a different angle
2. Output format: What structure the final report should take
3. Quality threshold: A score from 0.0 to 1.0 (default 0.75)
4. Domain hints: What domains this relates to (e.g., "technical", "business", "academic")

{format_instructions}

Research Question: {question}
"""
        
        if gap_analysis:
            prompt_text += f"""

Previous attempt failed. Gap analysis from Critic:
{gap_analysis}

Generate DIFFERENT, more targeted sub-queries that address these gaps specifically.
"""
        
        prompt_text += "\nRespond with ONLY the JSON matching the format above. No extra text."
        
        prompt = ChatPromptTemplate.from_template(prompt_text)
        self.last_token_usage = 0
        
        for attempt in range(3):
            try:
                messages = prompt.format_messages(
                    question=question,
                    format_instructions=self.parser.get_format_instructions()
                )
                ai_message = self.llm.invoke(messages)
                self.last_token_usage += extract_token_count(ai_message)
                
                result = self.parser.parse(ai_message.content)
                if not result.sub_queries:
                    result.sub_queries = [SubQuery(query=question, intent="Direct search")]
                if gap_analysis:
                    result.gap_analysis = gap_analysis
                return result
            except Exception as e:
                if attempt == 2:
                    return ResearchPlan(
                        sub_queries=[SubQuery(query=question, intent="Direct search")],
                        output_format="report",
                        quality_threshold=0.75,
                        gap_analysis=gap_analysis
                    )
                continue
        
        return ResearchPlan(
            sub_queries=[SubQuery(query=question, intent="Direct search")],
            output_format="report",
            quality_threshold=0.75,
            gap_analysis=gap_analysis
        )