#!/usr/bin/env python

from twisted.words.im import basechat, baseaccount, ircsupport
import logger
import settings
import botcommand

class MinConversation(basechat.Conversation):
    def show(self):
        pass
    
    def hide(self):
        pass
    
    def showMessage(self, text, metadata=None):
        logger.log(('<', self.person.name, '> ', text), (None, settings.cd['n'], None, settings.cd['pm']))
        bc = botcommand.BotCommand(self.person.name, text, self.sendText)
        bc.run()
        
    def contactChangedNick(self, person, newnick):
        logger.log((' -!- ', person.name, ' is now known as ', newnick), (settings.cd['a'], settings.cd['n'], None, settings.cd['n']))
        basechat.Conversation.contactChangedNick(self, person, newnick)

class MinGroupConversation(basechat.GroupConversation):
    def show(self):
        pass

    def hide(self):
        pass

    def showGroupMessage(self, sender, text, metadata=None):
        logger.log(('<', sender, '/', self.group.name, '> ', text), (None, settings.cd['n'], None, settings.cd['c'], None, settings.cd['cm']))
        bc = botcommand.BotCommand(sender, text, self.sendText)
        bc.run()

    def setTopic(self, topic, author):
        logger.log(('-!- ', author, ' set the ', self.group.name, ' topic to ', topic), (settings.cd['a'], settings.cd['n'], None, settings.cd['c'], None, settings.cd['t']))

    def memberJoined(self, member):
        logger.log(('-!- ', member, ' joined ', self.group.name), (settings.cd['a'], settings.cd['n'], None, settings.cd['c']))
        basechat.GroupConversation.memberJoined(self, member)

    def memberChangedNick(self, oldnick, newnick):
        logger.log(('-!- ', oldnick, ' in ', self.group.name, ' is now known as ', newnick), (settings.cd['a'], settings.cd['n'], None, settings.cd['c'], None, settings.cd['n']))
        basechat.GroupConversation.memberChangedNick(self, oldnick, newnick)

    def memberLeft(self, member):
        logger.log(('-!- ', member, ' left ', self.group.name), (settings.cd['a'], settings.cd['n'], None, settings.cd['c']))
        basechat.GroupConversation.memberLeft(self, member)

class MinChat(basechat.ChatUI):
    def getGroupConversation(self, group, Class=MinGroupConversation, 
        stayHidden=0):
        return basechat.ChatUI.getGroupConversation(self, group, Class, 
            stayHidden)
    def getConversation(self, person, Class=MinConversation, 
        stayHidden=0):
        return basechat.ChatUI.getConversation(self, person, Class, stayHidden)

class AccountManager (baseaccount.AccountManager):
    def __init__(self):
        self.chatui = MinChat()
        if len(settings.accounts) == 0:
            logger.log(("You have defined no settings.accounts.",), (settings.cd['e'],))
        for acct in settings.accounts:
            acct.logOn(self.chatui)

if __name__ == "__main__":
    from twisted.internet import reactor
    AccountManager()
    reactor.run()
