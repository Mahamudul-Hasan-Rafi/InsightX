// web/lib/types/interface/features/auth.interface.ts
//
// PURPOSE:
//   Types for the Keycloak OIDC calls the frontend makes itself (userinfo,
//   logout). Login / token / refresh are handled inside keycloak-js and are
//   not represented here.

/**
 * Response from the Keycloak `/userinfo` endpoint — standard OIDC claims plus
 * any extra realm/client claims. Index signature keeps non-standard claims
 * accessible without losing the typed common ones.
 */
export interface KeycloakUserInfo {
  sub: string;
  preferred_username?: string;
  name?: string;
  given_name?: string;
  family_name?: string;
  email?: string;
  email_verified?: boolean;
  [claim: string]: unknown;
}
