import multiprocessing


if __name__ == '__main__':
    multiprocessing.freeze_support()
    from app.desktop_entry import main
    raise SystemExit(main())
