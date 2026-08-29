# AI development disclosure

## Plain-English summary

This project was **vibe-coded by Mark Brown with OpenAI ChatGPT and Codex**.
AI assistance was a substantial part of creating it, not a token autocomplete
or an incidental editing tool.

## What the AI helped with

OpenAI ChatGPT and Codex assisted with:

- turning the project goal into an architecture and deployment plan;
- writing and revising the Python importer, Docker configuration and tests;
- researching Google Health and Open Wearables API compatibility;
- diagnosing validation failures from aggregate application logs;
- producing the public homepage, privacy wording, README, diagrams, security
  guidance, licence notes and Google OAuth checklist;
- operating the user-authorised test deployment and checking its results.

## What the human did

Mark Brown supplied the goal, requirements and person-centric product
direction; selected and controlled the infrastructure and accounts; made the
publication, privacy and credential decisions; granted Google access; and
approved deployment actions. AI-generated changes were exercised with
automated tests and a live self-hosted deployment before publication.

## Health data and AI

The released importer has **no AI runtime dependency or AI feature**. It does
not call OpenAI or another model provider, and it does not send health records
to an AI service. Its data path is Google Health → this self-hosted importer →
the operator's Open Wearables instance.

During development and troubleshooting, the user explicitly authorised the AI
assistant to inspect limited diagnostic output and requested health summaries
from the self-hosted instance. This was part of the development session, not an
automated behaviour of the software.

## Reliability and responsibility

Vibe-coded and AI-assisted software can contain plausible-looking mistakes.
Automated tests and successful deployment checks reduce but do not remove that
risk. Operators should review the source, protect credentials, maintain
backups, monitor imports, and avoid treating this software or its output as
medical advice.
