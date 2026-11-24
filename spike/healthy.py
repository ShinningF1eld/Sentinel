def on_designated_exception(connection, release, retained):
    release(connection)
