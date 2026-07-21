"""Unified prompt loader for all agent prompts.

All prompts are Jinja2 templates stored in templates/prompts/.
The agent_name is automatically fetched from AgentProfile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, Template

from anima_server.models import AgentProfile

PROMPTS_DIR = Path(__file__).with_name("templates") / "prompts"


class PromptLoader:
    """Loads and renders Jinja2 prompt templates with agent context."""

    def __init__(self, agent_name: str = "Anima"):
        self.agent_name = agent_name
        self._env = Environment(
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    @classmethod
    def from_db(cls, db_session, user_id: int) -> PromptLoader:
        """Create a PromptLoader with agent_name from the user's AgentProfile."""
        if db_session is None:
            return cls()
        profile = db_session.get(AgentProfile, user_id)
        agent_name = profile.agent_name if profile else "Anima"
        return cls(agent_name=agent_name)

    def _load_template(self, name: str) -> Template:
        """Load a template from the prompts directory."""
        path = PROMPTS_DIR / f"{name}.md.j2"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {name}")
        source = path.read_text(encoding="utf-8")
        return self._env.from_string(source)

    def render(self, template_name: str, **context: Any) -> str:
        """Render a prompt template with the given context.

        The agent_name is automatically added to the context.
        """
        template = self._load_template(template_name)
        full_context = {"agent_name": self.agent_name, **context}
        return template.render(**full_context).strip()

    # -----------------------------------------------------------------------
    # Reflection / Inner Monologue
    # -----------------------------------------------------------------------

    def quick_reflection(
        self,
        *,
        inner_state: str,
        emotional_synthesis: str,
        working_memory: str,
        recent_episodes: str,
        conversation: str,
    ) -> str:
        """Render the quick reflection prompt."""
        return self.render(
            "quick_reflection",
            inner_state=inner_state,
            emotional_synthesis=emotional_synthesis,
            working_memory=working_memory,
            recent_episodes=recent_episodes,
            conversation=conversation,
        )

    def quick_reflection_system(self) -> str:
        """Render the system prompt for quick reflection."""
        return self.render("quick_reflection_system")

    def deep_monologue(
        self,
        *,
        identity_version: int,
        identity: str,
        persona: str,
        inner_state: str,
        emotional_synthesis: str,
        working_memory: str,
        growth_log: str,
        intentions: str,
        user_facts: str,
        recent_episodes: str,
        emotional_signals: str,
    ) -> str:
        """Render the deep monologue prompt."""
        return self.render(
            "deep_monologue",
            identity_version=identity_version,
            identity=identity,
            persona=persona,
            inner_state=inner_state,
            emotional_synthesis=emotional_synthesis,
            working_memory=working_memory,
            growth_log=growth_log,
            intentions=intentions,
            user_facts=user_facts,
            recent_episodes=recent_episodes,
            emotional_signals=emotional_signals,
        )

    def deep_monologue_system(self) -> str:
        """Render the system prompt for deep monologue."""
        return self.render("deep_monologue_system")

    # -----------------------------------------------------------------------
    # Memory / Consolidation
    # -----------------------------------------------------------------------

    def memory_extraction(
        self,
        *,
        user_message: str,
        assistant_response: str,
    ) -> str:
        """Render the memory extraction prompt."""
        return self.render(
            "memory_extraction",
            user_message=user_message,
            assistant_response=assistant_response,
        )

    def conflict_check(self, *, existing: str, new_content: str) -> str:
        """Render the single conflict check prompt."""
        return self.render(
            "conflict_check",
            existing=existing,
            new_content=new_content,
        )

    def batch_conflict_check(
        self,
        *,
        existing_memories: str,
        new_content: str,
    ) -> str:
        """Render the batch conflict check prompt."""
        return self.render(
            "batch_conflict_check",
            existing_memories=existing_memories,
            new_content=new_content,
        )

    # -----------------------------------------------------------------------
    # Episode / Segmentation
    # -----------------------------------------------------------------------

    def batch_segmentation(self, *, messages: str) -> str:
        """Render the batch segmentation prompt."""
        return self.render(
            "batch_segmentation",
            messages=messages,
        )

    def episode_generation(
        self,
        *,
        turns: str,
        user_name: str = "the user",
        conversation_timestamp: str = "unknown",
    ) -> str:
        """Render the episode generation prompt."""
        return self.render(
            "episode_generation",
            turns=turns,
            user_name=user_name,
            conversation_timestamp=conversation_timestamp,
        )

    # -----------------------------------------------------------------------
    # Compaction / Summarization
    # -----------------------------------------------------------------------

    def summarization(self, *, transcript: str) -> str:
        """Render the conversation summarization prompt."""
        return self.render(
            "summarization",
            transcript=transcript,
        )

    # -----------------------------------------------------------------------
    # Proactive / Greeting
    # -----------------------------------------------------------------------

    def greeting(
        self,
        *,
        identity_context: str,
        emotional_context: str,
        time_context: str,
        task_context: str,
        memory_context: str,
    ) -> str:
        """Render the proactive greeting prompt."""
        return self.render(
            "greeting",
            identity_context=identity_context,
            emotional_context=emotional_context,
            time_context=time_context,
            task_context=task_context,
            memory_context=memory_context,
        )

    def reflection(
        self,
        *,
        identity_context: str,
        emotional_context: str,
        memory_context: str,
    ) -> str:
        """Render the reflection question prompt."""
        return self.render(
            "reflection",
            identity_context=identity_context,
            emotional_context=emotional_context,
            memory_context=memory_context,
        )

    def memory_curiosity(
        self,
        *,
        identity_context: str,
        episode_summary: str,
        episode_date: str,
    ) -> str:
        """Render the memory-curiosity prompt for a specific past episode."""
        return self.render(
            "memory_curiosity",
            identity_context=identity_context,
            episode_summary=episode_summary,
            episode_date=episode_date,
        )

    def greeting_pills(self, *, greeting_message: str, context: str) -> str:
        """Render the prompt that tags a greeting with a few short pills."""
        return self.render(
            "greeting_pills",
            greeting_message=greeting_message,
            context=context,
        )

    # -----------------------------------------------------------------------
    # Emotional Intelligence
    # -----------------------------------------------------------------------

    def emotion_extraction(self) -> str:
        """Render the emotion extraction prompt fragment."""
        return self.render("emotion_extraction")

    # -----------------------------------------------------------------------
    # Knowledge Graph
    # -----------------------------------------------------------------------

    def extract_entities(
        self,
        *,
        user_message: str,
        assistant_response: str,
    ) -> str:
        """Render the knowledge graph entity extraction prompt."""
        return self.render(
            "extract_entities",
            user_message=user_message,
            assistant_response=assistant_response,
        )

    def prune_relations(
        self,
        *,
        existing_relations: str,
        new_facts: str,
    ) -> str:
        """Render the knowledge graph relation pruning prompt."""
        return self.render(
            "prune_relations",
            existing_relations=existing_relations,
            new_facts=new_facts,
        )

    # -----------------------------------------------------------------------
    # Sleep Tasks / Maintenance
    # -----------------------------------------------------------------------

    def contradiction_check(
        self,
        *,
        memory_a: str,
        memory_b: str,
    ) -> str:
        """Render the memory contradiction check prompt."""
        return self.render(
            "contradiction_check",
            memory_a=memory_a,
            memory_b=memory_b,
        )

    def profile_synthesis(self, *, facts: str) -> str:
        """Render the profile synthesis prompt."""
        return self.render(
            "profile_synthesis",
            facts=facts,
        )

    def pattern_synthesis(self, *, episodes: str) -> str:
        """Render the cross-episode pattern synthesis prompt."""
        return self.render(
            "pattern_synthesis",
            episodes=episodes,
        )

    def latent_crystallization(self, *, topic_key: str, evidence: str) -> str:
        """Render the IL4 latent-trace crystallization prompt."""
        return self.render(
            "latent_crystallization",
            topic_key=topic_key,
            evidence=evidence,
        )

    # -----------------------------------------------------------------------
    # Predict-Calibrate (F3)
    # -----------------------------------------------------------------------

    def prediction(
        self,
        *,
        existing_facts: str,
        conversation_summary: str,
    ) -> str:
        """Render the prediction prompt for F3."""
        return self.render(
            "prediction",
            existing_facts=existing_facts,
            conversation_summary=conversation_summary,
        )

    def delta_extraction(
        self,
        *,
        prediction: str,
        user_message: str,
        assistant_response: str,
    ) -> str:
        """Render the delta extraction prompt for F3."""
        return self.render(
            "delta_extraction",
            prediction=prediction,
            user_message=user_message,
            assistant_response=assistant_response,
        )

    # -----------------------------------------------------------------------
    # Push initiative (IL3)
    # -----------------------------------------------------------------------

    def initiative_message(
        self,
        *,
        drive: str,
        material: str,
        affect_line: str,
    ) -> str:
        """Render the IL3 drive-tagged unprompted-message prompt.

        Carries the dominant drive tag, the current affect rendering, and
        the SPECIFIC accumulated material that drove the fire — and forbids
        generic check-in filler (see the template's hard rules).
        """
        return self.render(
            "initiative_message",
            drive=drive,
            material=material,
            affect_line=affect_line,
        )

    def dream_narrative(
        self,
        *,
        material: list[str],
        latent_topics: list[str],
        transcript_fragment: str | None,
        affect_line: str,
    ) -> str:
        """Render the IL7 dream-narrative prompt.

        Carries the important-but-cold memory fragments, any recurring latent
        undercurrents (structural topic keys — content-free), one old
        user-utterance fragment, and the current affect rendering. Asks for a
        JSON object with the narrative and bounded affect deltas.
        """
        return self.render(
            "dream_narrative",
            material=material,
            latent_topics=latent_topics,
            transcript_fragment=transcript_fragment,
            affect_line=affect_line,
        )


def get_prompt_loader(db_session, user_id: int) -> PromptLoader:
    """Get a PromptLoader instance for the given user."""
    return PromptLoader.from_db(db_session, user_id)
