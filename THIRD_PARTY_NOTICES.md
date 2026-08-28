# Third-party notices

This project interoperates with Open Wearables and uses the following Python
packages. They are not copied into this source repository; installations and
container builds obtain them from their normal distribution channels.

| Project | Licence |
| --- | --- |
| Open Wearables | MIT |
| FastAPI | MIT |
| HTTPX | BSD 3-Clause |
| Pydantic and pydantic-settings | MIT |
| Uvicorn | BSD 3-Clause |
| cryptography | Apache License 2.0 or BSD 3-Clause |

The Google Health API is a hosted service, not bundled software. Use of it is
subject to Google's API terms, OAuth policies, and API Services User Data
Policy in addition to this project's MIT licence.

The repository distributes source and build instructions, not prebuilt copies
of these packages. A party publishing a container image should generate an
SBOM for the exact build, preserve all required copyright/licence notices for
direct and transitive packages, and account for the Python and Debian base
image components. GitHub renders the README's Mermaid diagrams; Mermaid is not
bundled with this project. The banner artwork is original to this project.
