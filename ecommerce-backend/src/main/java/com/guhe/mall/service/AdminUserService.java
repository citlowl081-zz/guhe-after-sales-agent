package com.guhe.mall.service;

import com.guhe.mall.dto.AdminUserBalanceResponse;
import com.guhe.mall.dto.BalanceTransactionResponse;
import com.guhe.mall.entity.BalanceAccount;
import com.guhe.mall.entity.BalanceTransaction;
import com.guhe.mall.entity.UserProfile;
import com.guhe.mall.repository.BalanceAccountRepository;
import com.guhe.mall.repository.BalanceTransactionRepository;
import com.guhe.mall.repository.UserProfileRepository;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class AdminUserService {

    private final UserProfileRepository userProfileRepository;
    private final BalanceAccountRepository balanceAccountRepository;
    private final BalanceTransactionRepository balanceTransactionRepository;

    public AdminUserService(UserProfileRepository userProfileRepository,
                            BalanceAccountRepository balanceAccountRepository,
                            BalanceTransactionRepository balanceTransactionRepository) {
        this.userProfileRepository = userProfileRepository;
        this.balanceAccountRepository = balanceAccountRepository;
        this.balanceTransactionRepository = balanceTransactionRepository;
    }

    public AdminUserBalanceResponse getUserBalance(String userId) {
        UserProfile user = userProfileRepository.findByUserId(userId)
            .orElseThrow(() -> BusinessException.notFound("USER_NOT_FOUND", "用户不存在"));
        BalanceAccount account = balanceAccountRepository.findByUserId(userId)
            .orElseThrow(() -> BusinessException.notFound("BALANCE_ACCOUNT_NOT_FOUND", "余额账户不存在"));
        List<BalanceTransactionResponse> transactions = balanceTransactionRepository.findByUserIdOrderByCreatedAtDesc(userId)
            .stream()
            .limit(20)
            .map(this::toResponse)
            .toList();
        return new AdminUserBalanceResponse(user.getUserId(), user.getNickname(), user.getMobile(),
            user.getMemberLevel(), user.getRiskLevel(), account.getAvailableBalance(), transactions);
    }

    private BalanceTransactionResponse toResponse(BalanceTransaction transaction) {
        return new BalanceTransactionResponse(transaction.getTransactionNo(), transaction.getType(),
            transaction.getAmount(), transaction.getBalanceBefore(), transaction.getBalanceAfter(),
            transaction.getOrderNo(), transaction.getAfterSaleNo(), transaction.getRemark(), transaction.getCreatedAt());
    }
}
