CREATE TABLE fixture (
    id integer PRIMARY KEY,
    value text NOT NULL,
    version integer NOT NULL,
    instance text NOT NULL
);

INSERT INTO fixture (id, value, version, instance)
VALUES (1, 'ready', 1, gen_random_uuid()::text);
