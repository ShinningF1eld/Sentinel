def on_designated_exception(connection, release, retained):
    retained.append(connection)
