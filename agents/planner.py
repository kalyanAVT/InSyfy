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
from prompts.loader import load_prompt


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
        prompt_data = load_prompt("planner")
        system_text = prompt_data["system_template"]
        user_text = prompt_data["user_template"]
        
        if gap_analysis:
            user_text += (
                "\n\nPrevious attempt failed. Gap analysis from Critic:\n"
                "{gap_analysis}\n\n"
                "Generate DIFFERENT, more targeted sub-queries that address these gaps specifically."
            )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_text),
            ("human", user_text)
        ])
        self.last_token_usage = 0
        
        for attempt in range(3):
            try:
                messages = prompt.format_messages(
                    question=question,
                    format_instructions=self.parser.get_format_instructions(),
                    gap_analysis=gap_analysis
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