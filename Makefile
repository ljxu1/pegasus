ifndef prefix
prefix = $(PEGASUS_HOME)
endif
bindir = $(prefix)/bin

CXX = mpicxx
CC = $(CXX)
LD = $(CXX)
CXXFLAGS = -g -Wall
LDFLAGS = 
RM = rm -f
INSTALL = install
MAKE = make

# If you want to force the MPI processes to sleep when there is
# no message waiting instead of running in a busy loop, then
# enable -DSLEEP_IF_NO_REQUEST.
CXXFLAGS += -DSLEEP_IF_NO_REQUEST

# If you want to call fsyncdata() when a record is written to the 
# rescue log, then enable -DSYNC_RESCUE. If you want to enable
# it when data is written to a collective I/O file, then enable
# -DSYNC_IODATA. This will protect the application in the case
# where the system fails, but it adds quite a lot of overhead
# (typically ~10ms for most disks) per write. In the case of the
# rescue log, enabling SYNC_RESCUE causes PMC to handle no more 
# than about 100 tasks/second. Enabling SYNC_IODATA can reduce
# that even more.
#CXXFLAGS += -DSYNC_IODATA -DSYNC_RESCUE

OS=$(shell uname -s)
ifeq (Linux,$(OS))
  OPSYS = LINUX
endif
ifeq (Darwin,$(OS))
  OPSYS = DARWIN
endif
ifndef OPSYS
  $(error Unsupported OS: $(OS))
endif

CXXFLAGS += -D$(OPSYS)

OBJS += strlib.o
OBJS += tools.o
OBJS += failure.o
OBJS += engine.o
OBJS += dag.o
OBJS += master.o
OBJS += worker.o
OBJS += protocol.o
OBJS += log.o

PROGRAMS += pegasus-mpi-cluster

TESTS += test-strlib
TESTS += test-dag
TESTS += test-log
TESTS += test-engine
TESTS += test-tools
TESTS += test-fdcache

.PHONY: clean depends test install

ifeq ($(shell which $(CXX) || echo n),n)
$(warning To build pegasus-mpi-cluster set CXX to the path to your MPI C++ compiler wrapper)
all:
install:
else
all: $(PROGRAMS)
install: $(PROGRAMS)
	$(INSTALL) -m 0755 $(PROGRAMS) $(bindir)
endif

pegasus-mpi-cluster: pegasus-mpi-cluster.o $(OBJS)
test-strlib: test-strlib.o $(OBJS)
test-dag: test-dag.o $(OBJS)
test-log: test-log.o $(OBJS)
test-engine: test-engine.o $(OBJS)
test-tools: test-tools.o $(OBJS)
test-fdcache: test-fdcache.o $(OBJS)

test: $(TESTS) $(PROGRAMS)
	test/test.sh

distclean: clean
	$(RM) $(PROGRAMS)

clean:
	$(RM) *.o $(TESTS) svn.h

depends:
	g++ -MM *.cpp > depends.mk

svn.h:
	$(PWD)/gensvnh.sh $(PWD) > svn.h

tags: *.h *.cpp
	ctags *.h *.cpp

include depends.mk
