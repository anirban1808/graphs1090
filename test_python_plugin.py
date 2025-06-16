import collectd
import sys

def read_callback():
	collectd.info("collectd Python plugin test: Hello from Python {}!".format(sys.version_info.major))

collectd.register_read(read_callback, 10)

