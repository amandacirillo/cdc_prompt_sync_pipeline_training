-- Seed schema for the "support guideline" domain used by this training.
--
-- Hierarchy: queue -> scenario -> guideline_group (tiered) -> guideline
--
-- A `guideline` is either a `text` block (included verbatim in every
-- combination it belongs to) or a `toggle` (rendered as a Jinja-style
-- conditional referencing its own synced prompt, so it can be flipped on/off
-- per-combination later without re-syncing the guideline itself).
CREATE TABLE IF NOT EXISTS queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    external_queue_code VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    queue_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    FOREIGN KEY (queue_id) REFERENCES queue(id)
);

-- `tier` groups guideline_groups into cartesian-product "columns"
-- (e.g. tier 1 = tone, tier 2 = policy, tier 3 = escalation path). One
-- combination = exactly one guideline_group per tier.
CREATE TABLE IF NOT EXISTS guideline_group (
    id INT AUTO_INCREMENT PRIMARY KEY,
    scenario_id INT NOT NULL,
    tier INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    FOREIGN KEY (scenario_id) REFERENCES scenario(id)
);

CREATE TABLE IF NOT EXISTS guideline (
    id INT AUTO_INCREMENT PRIMARY KEY,
    guideline_group_id INT NOT NULL,
    type ENUM('text', 'toggle') NOT NULL DEFAULT 'text',
    guideline_text TEXT NOT NULL,
    priority INT NOT NULL DEFAULT 0,
    FOREIGN KEY (guideline_group_id) REFERENCES guideline_group(id)
);

-- Sample data: one queue, one scenario, two tiers, a handful of guidelines.
INSERT INTO queue (id, name, external_queue_code) VALUES
    (1, 'Billing Support', 'BILLING');

INSERT INTO scenario (id, queue_id, name) VALUES
    (1, 1, 'refund_request');

INSERT INTO guideline_group (id, scenario_id, tier, name) VALUES
    (1, 1, 1, 'Friendly Tone'),
    (2, 1, 1, 'Formal Tone'),
    (3, 1, 2, 'Standard Policy'),
    (4, 1, 2, 'Promotional Policy');

INSERT INTO guideline (id, guideline_group_id, type, guideline_text, priority) VALUES
    (1, 1, 'text', 'Use a warm, empathetic tone and address the customer by first name.', 10),
    (2, 2, 'text', 'Use formal, precise language and avoid contractions.', 10),
    (3, 3, 'text', 'Refunds are issued to the original payment method within 5-7 business days.', 20),
    (4, 3, 'toggle', 'Offer a loyalty credit in addition to the refund.', 30),
    (5, 4, 'text', 'Mention the current promotional retention offer before processing the refund.', 20);
