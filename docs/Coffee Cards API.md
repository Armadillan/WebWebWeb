# Coffee Card API

This document describes the backend API for the digital coffee card, so that the frontend can be implemented without reading the backend source.

## The feature

The guild sells physical coffee cards at the cafe: you pay for 10 coffees and get 11 (10 + 1 extra). A member crosses off a coffee on their own card when they get one (honor system), and a member can own several cards. The card can be handed over to another member.

The digital version works exactly the same:

- A **card** belongs to a user and contains **11 coffees** by default.
- The owner can **cross off** one coffee from a card (honor system).
- A card can be **transferred** to another user.
- A user can own **any number** of cards.
- Cards do not expire and cannot be refunded, so members cannot delete their own cards — only staff can, for example to remove a card that was added twice by mistake.

## Who is "cafe staff"?

Some actions may only be done by a cafe staff member (the card owner always acts on their own cards). The backend decides staff status in exactly one way:

A user is **cafe staff** if they either

1. have a permission that covers `manage Cafe` (i.e. `manage Cafe`, `manage all` or `super` on any target), **or**
2. are signed up for a **current** cafe shift (see `/cafe-shifts`), i.e. they are working at the cafe. A buffer is applied on each end of the shift so that staff can still act a short time before and after it: `CAFE_SHIFT_BUFFER_MINUTES` environment variable, default `15`.

This mirrors the physical world: whoever is working at the cafe can serve coffee and hand out cards, no matter which (or no) post they hold.

## Authentication

The API uses the same authentication as the rest of the backend: log in with `POST /auth/login` and send the access token as a `Bearer` token. All endpoints require a verified user. Unless noted otherwise, they additionally require the user to be a **member** (`is_member == true`).

Base path for this feature: `/coffee-cards`

## Schemas

### CoffeeCard

| field | type | description |
|---|---|---|
| `id` | int | The card's id |
| `user_id` | int | Id of the user who currently owns the card |
| `user` | object | `{id, first_name, last_name}` of the owner |
| `total_coffees` | int | How many coffees the card is worth (11 for a standard card) |
| `used_coffees` | int | How many coffees have been crossed off |
| `remaining_coffees` | int | `total_coffees - used_coffees` |
| `created_at` | string (UTC) | When the card was added to the user's profile |

Example:

```json
{
  "id": 4,
  "user_id": 17,
  "user": {"id": 17, "first_name": "Alice", "last_name": "Berg"},
  "total_coffees": 11,
  "used_coffees": 3,
  "remaining_coffees": 8,
  "created_at": "2025-01-05T09:30:00Z"
}
```

## Endpoints

### `GET /coffee-cards/mine`

Returns all cards owned by the logged-in user. Member only.

- Response `200`: list of `CoffeeCard`
- Response `401` / `403`: not logged in / not a member

### `GET /coffee-cards/{card_id}`

Returns one card.

- Allowed for the card owner and for cafe staff.
- Response `200`: `CoffeeCard`
- Response `404`: no such card
- Response `403`: logged in member who is neither the owner nor cafe staff
- Response `401`: not logged in

### `GET /coffee-cards/user/{user_id}`

Returns all cards of a given user.

- Allowed for the user themself and for cafe staff (e.g. to see all cards after scanning a member's QR code).
- Response `200`: list of `CoffeeCard`
- Response `404`: no such user
- Response `403`: not the user and not cafe staff

### `POST /coffee-cards/`

Adds a new card to a user's profile. This is what cafe staff do when a member buys a card (e.g. after scanning the member's QR code).

- **Cafe staff only.**
- Request body:

```json
{ "user_id": 17, "total_coffees": 11 }
```

  `total_coffees` is optional and defaults to `11`. It must be between `1` and `100`, which allows other card sizes in the future without backend changes.

- Response `200`: the new `CoffeeCard`
- Response `404`: no such user
- Response `403`: not cafe staff
- Response `401` / `422`: not logged in / invalid body

### `POST /coffee-cards/{card_id}/cross-off`

Crosses off one coffee on the card (no request body). The owner crosses off their own coffees, exactly like crossing off the physical card themselves.

- **Card owner only.**
- Response `200`: the updated `CoffeeCard`
- Response `404`: no such card
- Response `400`: the card is already empty
- Response `403`: not the owner

Crossing off is atomic on the backend: two simultaneous cross-offs can never drain a card past zero coffees.

### `POST /coffee-cards/{card_id}/transfer`

Transfers the card to another user, i.e. the new owner is the only one who can cross off from it afterwards.

- **Card owner only.**
- Request body:

```json
{ "user_id": 21 }
```

- Response `200`: the updated `CoffeeCard` (now with the new owner)
- Response `404`: no such card / no such user
- Response `403`: not the owner

### `DELETE /coffee-cards/{card_id}`

Deletes a card. **Cafe staff only** — for when a card was added by mistake (e.g. the QR code was scanned twice). A member can never delete their own card, since the coffees were already paid for.

- Response `204`: deleted
- Response `404`: no such card
- Response `403`: not cafe staff

## Suggested flows (frontend is free to do it differently)

The backend does not assume anything about how the QR code looks — the frontend may encode whatever it wants in it (a user id, a card id, a url, json, ...). The endpoints above are all keyed by plain `user_id` / `card_id` and can be combined to fit any scheme:

1. **Buying a card at the cafe.** The member shows their QR code in the app (e.g. on their profile page). A staff member scans it, looks up the member, and calls `POST /coffee-cards/` with the member's `user_id`. The card shows up in the member's profile.
2. **Getting a coffee.** The owner opens their profile and taps "cross off" on a card → `POST /coffee-cards/{card_id}/cross-off`. This works at the cafe too: the member crosses off their own coffee themselves (honor system), and the staff member simply looks over it (they can view the card by `card_id`, or list the user's cards by `user_id`).
3. **Gifting a card.** The owner transfers it to the recipient's profile.
4. **Admin work.** Staff can list and view a user's cards, add cards, or delete a card — useful for fixing double scans or handling edge cases.

## Errors

Errors use the standard FastAPI format:

```json
{"detail": "Card is empty"}
```

| code | meaning |
|---|---|
| 400 | Bad request (e.g. crossing off an empty card) |
| 401 | Not logged in |
| 403 | Logged in but not allowed (not a member, not the owner, not cafe staff) |
| 404 | Card or user does not exist |
| 422 | Invalid request body |
