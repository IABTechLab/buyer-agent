# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Social media channel specialist agent (Meta Ads)."""

from crewai import Agent

from ...config.settings import settings


def create_social_agent() -> Agent:
    """Create the Social Media Specialist agent for Meta Ads campaigns."""
    return Agent(
        role="Social Media Advertising Specialist",
        goal=(
            "Identify the best Meta Ads placements (Facebook, Instagram, Audience Network) "
            "for campaigns targeting social media audiences. Evaluate reach, CPM, and "
            "audience alignment. Only recommend placements actually returned by the "
            "search_meta_placements tool."
        ),
        backstory=(
            "Expert in Meta Ads ecosystem with deep knowledge of Facebook Feed, Instagram Reels, "
            "Stories, and Audience Network inventory. Skilled at matching campaign objectives "
            "(brand awareness, reach, conversions) to optimal Meta placements and bidding strategies."
        ),
        llm=settings.manager_llm_model,
        verbose=settings.crew_verbose,
        allow_delegation=False,
        max_iter=settings.crew_max_iterations,
    )
