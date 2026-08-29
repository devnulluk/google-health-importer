# Google OAuth publication checklist

This checklist covers deployment configuration that cannot be enforced by the
source code. It is not legal advice and does not replace Google's policies.

## Application identity

- [ ] Use an accurate application name and identify the operator.
- [ ] Host the homepage on a verified domain owned by the operator.
- [ ] Set the OAuth homepage to `https://your-importer.example/`.
- [ ] Set the privacy-policy URL to `https://your-importer.example/privacy`.
- [ ] Ensure the public contact email is monitored.
- [ ] Keep Google Cloud project contacts current.

## Consent and scopes

- [ ] Configure exactly the three read-only scopes requested by the code.
- [ ] Explain that metrics, activity, workouts and sleep are copied to the user's configured Open
      Wearables instance.
- [ ] Do not add scopes for possible future features.
- [ ] Demonstrate the complete OAuth flow if Google requests a verification
      video.

## Data handling

- [ ] Run behind HTTPS and restrict administrative endpoints.
- [ ] Keep OAuth secrets, API keys and encryption keys outside Git.
- [ ] Protect the persistent volume and backups as sensitive data.
- [ ] Test `POST /disconnect` and document deletion in Open Wearables.
- [ ] Never use Google health data for advertising, sale, surveillance, or
      general-purpose AI training.

## Publication status

- [ ] Personal/test use: keep the OAuth audience appropriately restricted.
- [ ] Wider public use: submit brand and sensitive-scope verification before
      launch when required by Google.
- [ ] Re-check verification requirements after changing the app name, logo,
      domains, privacy URL, redirect URI, or scopes.

Official references:

- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)
- [Google Health API Developer and User Data Policy](https://developers.google.com/health/policies/health-api-developer-user-data-policy)
- [Google Health scopes](https://developers.google.com/health/scopes)
- [OAuth verification requirements](https://support.google.com/cloud/answer/13464321)
- [When verification is not needed](https://support.google.com/cloud/answer/13464323)
