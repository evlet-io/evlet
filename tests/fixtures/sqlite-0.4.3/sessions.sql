BEGIN TRANSACTION;
CREATE TABLE evlet_login_limits (client TEXT PRIMARY KEY, attempts INTEGER NOT NULL, resets INTEGER NOT NULL);
CREATE TABLE evlet_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL, realm TEXT NOT NULL DEFAULT 'desk', started INTEGER, agent TEXT NOT NULL DEFAULT '', two_step INTEGER);
CREATE TABLE evlet_turn (id INTEGER PRIMARY KEY);
CREATE TABLE evlet_two_step_tickets (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, realm TEXT NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, secret BLOB);
CREATE INDEX evlet_session_user ON evlet_sessions(user_id);
CREATE INDEX evlet_session_expiry ON evlet_sessions(expires);
CREATE INDEX evlet_login_reset ON evlet_login_limits(resets);
COMMIT;
