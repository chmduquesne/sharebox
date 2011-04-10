#!/usr/bin/env python

# First run tutorial.glade through gtk-builder-convert with this command:
# gtk-builder-convert tutorial.glade tutorial.xml
# Then save this file as tutorial.py and make it executable using this command:
# chmod a+x tutorial.py
# And execute it:
# ./tutorial.py

import pygtk
pygtk.require("2.0")
import gtk

class Line(gtk.HBox):
    def __init__(self, path):
        gtk.HBox.__init__(self)
        self.path = path
        builder = gtk.Builder()
        builder.add_from_file("line.xml")
        self.line = builder.get_object("line_hbox")
        self.keep_local_button = builder.get_object("keep_local_button")
        self.keep_remote_button = builder.get_object("keep_remote_button")
        label = builder.get_object("path_label")
        label.set_text(path)
        self.line.unparent()
        builder.connect_signals(self)

    def on_open_local_button_clicked(self, widget, data=None):
        print "open local " + self.path

    def on_open_remote_button_clicked(self, widget, data=None):
        print "open remote " + self.path

class MergerApp(object):

    def __init__(self, files):
        builder = gtk.Builder()
        builder.add_from_file("merger.xml")
        self.window = builder.get_object("dialog")
        self.vbox = builder.get_object("vbox1")
        self.keep_local_button = builder.get_object("keep_local_button")
        self.keep_remote_button = builder.get_object("keep_remote_button")
        builder.connect_signals(self)
        self.lines = []
        for i in files:
            self.add_line(i)
        self.window.show()

    def add_line(self, path):
        line = Line(path)
        self.lines.append(line)
        child = line.line
        self.vbox.pack_end(child)
        for i in self.vbox.get_children():
            i.set_visible(True)

    def on_dialog_destroy(self, widget, data=None):
        gtk.main_quit()

    def on_global_keep_button_toggled(self, widget, data=None):
        if self.keep_local_button.get_active():
            for i in self.lines:
                i.keep_local_button.set_active(True)
        elif self.keep_remote_button.get_active():
            for i in self.lines:
                i.keep_remote_button.set_active(True)

    def on_ok_button_clicked(self, widget, data=None):
        print "exiting ok"
        print self.get_user_choices()
        gtk.main_quit()

    def on_cancel_button_clicked(self, widget, data=None):
        print "exiting ko"
        gtk.main_quit()

    def get_user_choices(self):
        res = {}
        for i in self.lines:
            if i.keep_local_button.get_active():
                res[ i.path ] = "local"
            else:
                res[ i.path ] = "remote"
        return res


if __name__ == "__main__":
    app = MergerApp(['foo/bar', 'foo/baz', 'foo/baz/zoo'])
    gtk.main()
