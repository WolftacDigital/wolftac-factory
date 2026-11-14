# Provider OAuth contract preflight

Before implementing a separate feature OAuth grant that shares an existing sign-in Cloud project, verify the provider's revocation boundary against the spec's independence promise. A separate callback, token, or OAuth client does not necessarily isolate revocation.

For Google, the Web Server OAuth guide's **Token revocation** section documents that revocation removes the user's OAuth scopes granted to the project and invalidates issued access/refresh tokens across clients registered in that project:
https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke

- Recheck the live documentation before relying on this behavior.
- If the spec requires both project-shared credentials and independently revocable feature grants, obtain a work-order decision through the stage's BLOCKED path. Do not silently change credential/project architecture or waive isolation.
- Distinguish Google grant/token revocation from the application's own session cookie. A JWT-based app session is not proven destroyed merely because a Google token was revoked.
- Do not exercise revocation against real credentials to establish this contract. Mock-based tests can verify the requested endpoint call, but cannot establish provider-side isolation that the provider does not promise.
- If extraction returns only the beginning of a long guide or drops the section anchor, read the rendered section in the browser. Search for the heading actually present (for example `Token revocation`), rather than assuming an older heading remains unchanged.
